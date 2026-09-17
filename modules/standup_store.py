"""Persistent morning standup roster and daily preparation notes."""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path

from config import APP_DIR

DEFAULT_PEOPLE = [
    ("東", "あずま"), ("大島", "おおじま"), ("小川", "おがわ"), ("北野", "きたの"),
    ("小島", "こじま"), ("酒井", "さかい"), ("佐藤", "さとう"), ("芝", "しば"),
    ("祖父江", "そふえ"), ("タイラー", "タイラー"), ("田中", "たなか"), ("堂内", "どううち"),
    ("野崎", "のざき"), ("藤原", "ふじはら"), ("柾木", "まさき"), ("森竹", "もりたけ"),
]

class StandupStore:
    def __init__(self, path: Path | None = None):
        self.path = path or APP_DIR / "standup.json"; self.data = self._load()

    def _default(self) -> dict:
        return {"people": [{"id": index + 1, "name": name, "reading": reading, "order": index} for index, (name, reading) in enumerate(DEFAULT_PEOPLE)], "entries": {}}

    def _load(self) -> dict:
        try: data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError): data = self._default()
        if not isinstance(data, dict): data = self._default()
        if not isinstance(data.get("people"), list) or not data["people"]: data["people"] = self._default()["people"]
        if not isinstance(data.get("entries"), dict): data["entries"] = {}
        return data

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True); temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8"); temporary.replace(self.path)

    def people(self, day: str | None = None) -> list[dict]:
        day = day or date.today().isoformat(); checked = set(self.entry(day).get("checked_people", []))
        values = sorted(self.data["people"], key=lambda person: (int(person.get("id")) in checked, person.get("order", 0)))
        return [person | {"checked": int(person["id"]) in checked} for person in values]

    def entry(self, day: str | None = None) -> dict:
        day = day or date.today().isoformat()
        defaults = {"date": day, "what_i_will_say": "", "yesterday": "", "today": "", "blockers": "", "notes": "", "checked_people": []}
        return defaults | dict(self.data["entries"].get(day, {}))

    def save_entry(self, day: str, values: dict) -> None:
        current = self.entry(day); current.update(values); current["date"] = day; self.data["entries"][day] = current; self.save()

    def toggle_person(self, person_id: int, day: str | None = None) -> bool:
        day = day or date.today().isoformat(); entry = self.entry(day); checked = set(entry["checked_people"])
        if person_id in checked: checked.remove(person_id); value = False
        else: checked.add(person_id); value = True
        entry["checked_people"] = sorted(checked); self.data["entries"][day] = entry; self.save(); return value

    def reset_people(self, day: str | None = None) -> None:
        day = day or date.today().isoformat(); entry = self.entry(day); entry["checked_people"] = []; self.data["entries"][day] = entry; self.save()

    def add_person(self, name: str, reading: str = "") -> dict:
        next_id = max((int(person.get("id", 0)) for person in self.data["people"]), default=0) + 1
        person = {"id": next_id, "name": name.strip(), "reading": reading.strip(), "order": len(self.data["people"])}; self.data["people"].append(person); self.save(); return person

    def remove_person(self, person_id: int) -> None:
        self.data["people"] = [person for person in self.data["people"] if int(person.get("id")) != person_id]
        for entry in self.data["entries"].values(): entry["checked_people"] = [value for value in entry.get("checked_people", []) if value != person_id]
        self.save()
