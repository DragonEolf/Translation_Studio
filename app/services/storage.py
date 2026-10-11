import json
from pathlib import Path


class Storage:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, document_id, filename):
        folder = self.root / document_id
        folder.mkdir(exist_ok=True)
        return folder / filename

    def write_json(self, document_id, name, data):
        path = self.path(document_id, name)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
