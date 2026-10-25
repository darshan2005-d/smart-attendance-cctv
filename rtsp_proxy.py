"""
rtsp_proxy.py
=============
Local RTSP proxy that handles SHA-256 Digest Authentication with Prama/Hikvision
IP cameras and re-exposes the stream unauthenticated on 127.0.0.1:8554.

WHY THIS EXISTS:
  OpenCV's bundled FFmpeg only supports MD5 Digest Auth for RTSP.
  Prama India (Hikvision OEM) cameras enforce SHA-256 Digest Auth.
  This proxy intercepts OpenCV's plain RTSP connection, performs the SHA-256
  handshake with the real camera, and transparently pipes RTP frames back.

USAGE:
  proxy = RtspSha256Proxy()
  proxy.configure(ip, port, username, password, rtsp_path)
  proxy.start()
  # OpenCV now connects to: rtsp://127.0.0.1:8554/live
  proxy.stop()
"""

import hashlib
import re
import socket
import threading
import time
from typing import Optional

# Proxy always listens on this local address
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 8554
PROXY_RTSP_URL = f"rtsp://{PROXY_HOST}:{PROXY_PORT}/live"


def _sha256_digest_auth(username: str, password: str, realm: str, nonce: str,
                        method: str, uri: str) -> str:
    """Computes RFC 7616 SHA-256 Digest Auth response header value."""
    ha1 = hashlib.sha256(f"{username}:{realm}:{password}".encode()).hexdigest()
    ha2 = hashlib.sha256(f"{method}:{uri}".encode()).hexdigest()
    response = hashlib.sha256(f"{ha1}:{nonce}:{ha2}".encode()).hexdigest()
    return (
        f'Digest username="{username}", realm="{realm}", nonce="{nonce}", '
        f'uri="{uri}", response="{response}", algorithm="SHA-256"'
    )


class RtspSha256Proxy:
    """
    Listens on 127.0.0.1:8554 for any RTSP client (OpenCV/FFmpeg),
    performs SHA-256 digest authentication with the real camera on behalf
    of the client, and pipes the raw RTP stream through transparently.
    """

    def __init__(self):
        self._ip: str = ""
        self._port: int = 554
        self._username: str = ""
        self._password: str = ""
        self._rtsp_path: str = "/Streaming/Channels/102"
        self._running: bool = False
        self._server_sock: Optional[socket.socket] = None
        self._accept_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def configure(self, ip: str, port: int, username: str, password: str,
                  rtsp_path: str = "/Streaming/Channels/102"):
        """Call before start() to set camera credentials."""
        with self._lock:
            self._ip = ip
            self._port = port
            self._username = username
            self._password = password
            self._rtsp_path = rtsp_path
        print(f"[Proxy] Configured: rtsp://{ip}:{port}{rtsp_path} (user={username})")

    def start(self):
        """Binds the proxy server socket and starts accepting connections."""
        with self._lock:
            if self._running:
                return
            if not self._ip:
                print("[Proxy] ERROR: Call configure() before start().")
                return

            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((PROXY_HOST, PROXY_PORT))
                sock.listen(5)
                self._server_sock = sock
                self._running = True
                print(f"[Proxy] Listening on {PROXY_RTSP_URL}")
            except OSError as e:
                print(f"[Proxy] Failed to bind on port {PROXY_PORT}: {e}")
                return

        self._accept_thread = threading.Thread(
            target=self._accept_loop, daemon=True, name="rtsp-proxy-accept"
        )
        self._accept_thread.start()

    def stop(self):
        """Stops the proxy and closes the server socket."""
        with self._lock:
            self._running = False
            if self._server_sock:
                try:
                    self._server_sock.close()
                except Exception:
                    pass
                self._server_sock = None
        print("[Proxy] Stopped.")

    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Internal accept loop
    # ------------------------------------------------------------------

    def _accept_loop(self):
        while self._running:
            try:
                client_sock, addr = self._server_sock.accept()
                print(f"[Proxy] Client connected from {addr}")
                t = threading.Thread(
                    target=self._handle_client,
                    args=(client_sock,),
                    daemon=True,
                    name=f"rtsp-proxy-client-{addr[1]}"
                )
                t.start()
            except Exception:
                break

    # ------------------------------------------------------------------
    # Per-client session handler
    # ------------------------------------------------------------------

    def _handle_client(self, client_sock: socket.socket):
        cam_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cam_sock.settimeout(8.0)

        # Capture current config so the session is consistent
        with self._lock:
            ip = self._ip
            port = self._port
            username = self._username
            password = self._password
            rtsp_path = self._rtsp_path

        cam_url = f"rtsp://{ip}:{port}{rtsp_path}"
        track_url = f"{cam_url}/trackID=1"

        # State for the RTSP exchange
        realm = ""
        nonce = ""
        cam_session = ""

        try:
            cam_sock.connect((ip, port))
        except Exception as e:
            print(f"[Proxy] Cannot connect to camera {ip}:{port} - {e}")
            client_sock.close()
            cam_sock.close()
            return

        client_sock.settimeout(10.0)
        streaming = False

        try:
            while self._running and not streaming:
                # ---- Receive one full RTSP request from OpenCV ----
                try:
                    raw = client_sock.recv(4096)
                except socket.timeout:
                    continue
                if not raw:
                    break

                req_str = raw.decode(errors="ignore")
                lines = req_str.strip().splitlines()
                if not lines:
                    continue

                method = lines[0].split()[0] if lines[0].split() else ""
                cseq_match = re.search(r'CSeq:\s*([0-9]+)', req_str, re.IGNORECASE)
                cseq = cseq_match.group(1) if cseq_match else "1"

                print(f"[Proxy] << {method} (CSeq {cseq})")

                # ---- Handle each RTSP method ----

                if method == "OPTIONS":
                    resp = (
                        f"RTSP/1.0 200 OK\r\n"
                        f"CSeq: {cseq}\r\n"
                        f"Public: OPTIONS, DESCRIBE, SETUP, TEARDOWN, PLAY, PAUSE\r\n\r\n"
                    )
                    client_sock.sendall(resp.encode())

                elif method == "DESCRIBE":
                    # Step 1: Get 401 challenge from camera
                    cam_sock.sendall(
                        f"DESCRIBE {cam_url} RTSP/1.0\r\nCSeq: 1\r\nAccept: application/sdp\r\n\r\n".encode()
                    )
                    c401 = _recv_response(cam_sock)
                    realm_m = re.search(r'realm="([^"]+)"', c401)
                    nonce_m = re.search(r'nonce="([^"]+)"', c401)
                    if not realm_m or not nonce_m:
                        print(f"[Proxy] Unexpected camera response to DESCRIBE: {c401[:200]}")
                        break
                    realm = realm_m.group(1)
                    nonce = nonce_m.group(1)

                    # Step 2: Send authenticated DESCRIBE
                    auth = _sha256_digest_auth(username, password, realm, nonce, "DESCRIBE", cam_url)
                    cam_sock.sendall(
                        f"DESCRIBE {cam_url} RTSP/1.0\r\n"
                        f"CSeq: 2\r\n"
                        f"Accept: application/sdp\r\n"
                        f"Authorization: {auth}\r\n\r\n".encode()
                    )
                    sdp_resp = _recv_response(cam_sock, body=True)

                    # Split headers and SDP body
                    if "\r\n\r\n" in sdp_resp:
                        sdp_hdr, sdp_body = sdp_resp.split("\r\n\r\n", 1)
                    else:
                        sdp_hdr, sdp_body = sdp_resp, ""

                    # Rewrite control URLs to point at our proxy
                    sdp_body = re.sub(
                        r'a=control:rtsp://[0-9A-Za-z.:\-]+/[^\s]+',
                        f'a=control:rtsp://{PROXY_HOST}:{PROXY_PORT}/live/trackID=1',
                        sdp_body
                    )
                    sdp_body = re.sub(
                        r'a=control:trackID=([0-9]+)',
                        r'a=control:trackID=\1',
                        sdp_body
                    )

                    sdp_bytes = sdp_body.encode()
                    out = (
                        f"RTSP/1.0 200 OK\r\n"
                        f"CSeq: {cseq}\r\n"
                        f"Content-Type: application/sdp\r\n"
                        f"Content-Base: rtsp://{PROXY_HOST}:{PROXY_PORT}/live/\r\n"
                        f"Content-Length: {len(sdp_bytes)}\r\n\r\n"
                    ).encode() + sdp_bytes
                    client_sock.sendall(out)
                    print(f"[Proxy] >> DESCRIBE 200 OK (SDP {len(sdp_bytes)} bytes)")

                elif method == "SETUP":
                    if not realm or not nonce:
                        # Re-do DESCRIBE first (shouldn't normally happen)
                        break

                    auth = _sha256_digest_auth(username, password, realm, nonce, "SETUP", track_url)
                    cam_sock.sendall(
                        f"SETUP {track_url} RTSP/1.0\r\n"
                        f"CSeq: 3\r\n"
                        f"Transport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n"
                        f"Authorization: {auth}\r\n\r\n".encode()
                    )
                    c_resp = _recv_response(cam_sock)
                    sess_m = re.search(r'Session:\s*([a-zA-Z0-9_\-]+)', c_resp)
                    if not sess_m:
                        print(f"[Proxy] No session in SETUP response: {c_resp[:200]}")
                        break
                    cam_session = sess_m.group(1)

                    out = (
                        f"RTSP/1.0 200 OK\r\n"
                        f"CSeq: {cseq}\r\n"
                        f"Transport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n"
                        f"Session: {cam_session}\r\n\r\n"
                    )
                    client_sock.sendall(out.encode())
                    print(f"[Proxy] >> SETUP 200 OK (Session {cam_session})")

                elif method == "PLAY":
                    auth = _sha256_digest_auth(username, password, realm, nonce, "PLAY", cam_url)
                    cam_sock.sendall(
                        f"PLAY {cam_url} RTSP/1.0\r\n"
                        f"CSeq: 4\r\n"
                        f"Session: {cam_session}\r\n"
                        f"Range: npt=0.000-\r\n"
                        f"Authorization: {auth}\r\n\r\n".encode()
                    )
                    _recv_response(cam_sock)  # Camera's 200 OK for PLAY

                    out = (
                        f"RTSP/1.0 200 OK\r\n"
                        f"CSeq: {cseq}\r\n"
                        f"Session: {cam_session}\r\n\r\n"
                    )
                    client_sock.sendall(out.encode())
                    print("[Proxy] >> PLAY 200 OK - starting RTP pipe")

                    # Switch to raw bidirectional pipe
                    streaming = True
                    _pipe_bidirectional(cam_sock, client_sock, self)
                    return

                elif method == "TEARDOWN":
                    out = f"RTSP/1.0 200 OK\r\nCSeq: {cseq}\r\n\r\n"
                    client_sock.sendall(out.encode())
                    break

                else:
                    # Unknown method - return 501
                    out = f"RTSP/1.0 501 Not Implemented\r\nCSeq: {cseq}\r\n\r\n"
                    client_sock.sendall(out.encode())

        except Exception as e:
            print(f"[Proxy] Session error: {e}")
        finally:
            try:
                client_sock.close()
            except Exception:
                pass
            try:
                cam_sock.close()
            except Exception:
                pass
            print("[Proxy] Client session ended.")


# ---------------------------------------------------------------------------
# Helper: receive a full RTSP response (headers + optional body)
# ---------------------------------------------------------------------------

def _recv_response(sock: socket.socket, body: bool = False, timeout: float = 6.0) -> str:
    """
    Reads bytes until the double-CRLF end-of-headers, then reads Content-Length
    bytes if body=True and Content-Length is present.
    """
    sock.settimeout(timeout)
    buf = b""
    deadline = time.time() + timeout
    while b"\r\n\r\n" not in buf:
        if time.time() > deadline:
            break
        try:
            chunk = sock.recv(2048)
            if not chunk:
                break
            buf += chunk
        except socket.timeout:
            break

    if body and b"\r\n\r\n" in buf:
        headers_raw, rest = buf.split(b"\r\n\r\n", 1)
        cl_match = re.search(rb'Content-Length:\s*([0-9]+)', headers_raw, re.IGNORECASE)
        if cl_match:
            content_length = int(cl_match.group(1))
            deadline2 = time.time() + timeout
            while len(rest) < content_length and time.time() < deadline2:
                try:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    rest += chunk
                except socket.timeout:
                    break
            return headers_raw.decode(errors="ignore") + "\r\n\r\n" + rest.decode(errors="ignore")

    return buf.decode(errors="ignore")


# ---------------------------------------------------------------------------
# Helper: bidirectional RTP pipe (camera -> client only in practice)
# ---------------------------------------------------------------------------

def _pipe_bidirectional(cam_sock: socket.socket, client_sock: socket.socket,
                        proxy: RtspSha256Proxy):
    """Pipes raw bytes from camera to OpenCV client until either side closes."""
    cam_sock.settimeout(None)
    client_sock.settimeout(None)
    stop_event = threading.Event()

    def cam_to_client():
        try:
            while proxy.is_running() and not stop_event.is_set():
                data = cam_sock.recv(65536)
                if not data:
                    break
                client_sock.sendall(data)
        except Exception:
            pass
        stop_event.set()
        print("[Proxy] RTP cam->client pipe closed.")

    def client_to_cam():
        """Forward RTCP or teardown from OpenCV back to the camera."""
        try:
            while proxy.is_running() and not stop_event.is_set():
                data = client_sock.recv(4096)
                if not data:
                    break
                cam_sock.sendall(data)
        except Exception:
            pass
        stop_event.set()

    t1 = threading.Thread(target=cam_to_client, daemon=True, name="rtp-cam->client")
    t2 = threading.Thread(target=client_to_cam, daemon=True, name="rtp-client->cam")
    t1.start()
    t2.start()
    t1.join()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

proxy = RtspSha256Proxy()

