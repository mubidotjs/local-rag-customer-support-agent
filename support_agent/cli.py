import argparse
import json
from pathlib import Path
from .config import SETTINGS
from .ollama import Ollama
from .store import ingest


def main():
    parser = argparse.ArgumentParser(description="Ingest explicitly selected local documentation (no downloads)")
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(json.dumps(ingest(args.directory, SETTINGS, Ollama(SETTINGS)), indent=2))


if __name__ == "__main__":
    main()
