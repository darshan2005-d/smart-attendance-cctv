import sys
from recognition_engine import engine
import database

def main():
    print("=" * 60)
    print("   TRAINING FACE RECOGNITION BIOMETRIC EMBEDDINGS")
    print("=" * 60)

    database.init_db()
    result = engine.train()

    print("\n------------------------------------------------------------")
    print(f"Status          : {result.get('message')}")
    print(f"Total Encodings : {result.get('total_encodings')}")
    print(f"Enrolled Faces  : {result.get('total_students')} students ({result.get('total_images')} photos)")
    print("Saved Model     : trainer/encodings.pickle")
    print("------------------------------------------------------------\n")

if __name__ == "__main__":
    main()
