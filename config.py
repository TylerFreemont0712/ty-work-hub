"""Local preferences. Keep API and environment credentials in the OS keychain."""
from __future__ import annotations
import json
import os
from pathlib import Path

# Pure data modules: the single sources of valid themes and schedule shapes.
from modules.work_schedule import DEFAULT_BREAKS, DEFAULT_DAYS, DEFAULT_END, DEFAULT_START, WorkSchedule
from ui.theme import DEFAULT_THEME, THEMES

APP_DIR = Path(os.getenv("TY_WORK_APP_HOME", Path.home() / ".ty-work-app"))
CONFIG_PATH = APP_DIR / "config.json"

# Values left over from the Ollama-based pipeline. They cannot work against the
# llama.cpp server and are replaced on load rather than failing at generation.
OLLAMA_URLS = {"http://192.168.17.201:11434", "http://localhost:11434", "http://127.0.0.1:11434"}
OLLAMA_MODEL_TAGS = {"MiraxOrnith64k:latest", "MiraxOrnith:latest", "MiraX-Qwen3.6-27B-65536ctx:latest"}
DEFAULTS = {
    "obsidian_vault_path": "",
    "polling_interval_minutes": 10,
    "theme": DEFAULT_THEME,
    "custom_launchers": [],
    "saved_task_views": [],
    "calendar_default_view": "Week",
    "calendar_default_minutes": 30,
    "work_schedule": {
        "start": DEFAULT_START,
        "end": DEFAULT_END,
        "days": list(DEFAULT_DAYS),
        "breaks": [dict(item) for item in DEFAULT_BREAKS],
    },
    "track_business_hours_only": True,
    "general_project": "Company General",
    "show_weekends": True,
    "focus_mode": False,
    "backlog_base_url": "https://your-space.backlog.com/api/v2",
    "backlog_web_url": "https://your-space.backlog.com",
    "backlog_assignee_id": 0,
    "backlog_hide_closed": True,
    "obsidian_tickets_folder": "Work/Tickets",
    "obsidian_daily_folder": "Work/Daily",
    "obsidian_programming_folder": "Work/Programming",
    "local_llm_url": "http://localhost:8080",
    "local_llm_model": "",
    "local_llm_timeout_seconds": 600,
    "local_llm_thinking": False,
    "local_llm_max_tokens": 8192,
    "local_llm_context_tokens": 32768,
    "default_page": "Command Center",
    "density": "comfortable",
    "font_size": 12,
    "navigation_collapsed": False,
    "task_filter_panel": False,
    "task_view": "all",
    "task_columns": ["priority", "status", "due", "project", "assignee"],
    "environment_profiles": {},
    "source_control_provider": "git",
    "git_executable": "",
    "git_repository": "",
    "svn_executable": "",
    "tortoise_executable": "",
    "svn_patch_root": str(APP_DIR / "svn" / "patches"),
    "svn_timeout_seconds": 120,
    "svn_profiles": [],
    "svn_show_unversioned": True,
    "svn_shelf_all_tickets": True,
    "svn_shelf_sort": [],
}

def _work_schedule(saved: dict) -> WorkSchedule:
    """Normalize the schedule from the saved file, seeding it from the old hours.

    Takes the file contents rather than the merged configuration: after merging,
    the default schedule is always present and the migration could never run.
    """
    if isinstance(saved.get("work_schedule"), dict):
        return WorkSchedule.from_config(saved)
    # Files written before schedules stored two plain hour numbers.
    legacy = dict(DEFAULTS["work_schedule"])
    for key, field in (("workday_start", "start"), ("workday_end", "end")):
        try:
            hour = int(saved[key])
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= hour <= 23:
            legacy[field] = f"{hour:02d}:00"
    return WorkSchedule.from_config({"work_schedule": legacy})


def load_config() -> dict:
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    config = DEFAULTS | (data if isinstance(data, dict) else {})
    # Existing SVN users keep their default; new installations start with Git.
    if isinstance(data, dict) and "source_control_provider" not in data and data.get("svn_profiles"):
        config["source_control_provider"] = "svn"
    if config["source_control_provider"] not in {"git", "svn"}:
        config["source_control_provider"] = "git"
    for field in ("git_executable", "git_repository"):
        config[field] = str(config.get(field) or "").strip().strip('"')
    config["theme"] = config["theme"] if config["theme"] in THEMES else DEFAULTS["theme"]
    config["calendar_default_view"] = config["calendar_default_view"] if config["calendar_default_view"] in {"Month", "Week", "Day", "Agenda"} else DEFAULTS["calendar_default_view"]
    config["default_page"] = config["default_page"] if config["default_page"] in {"Command Center", "Personal Tasks", "Ticket Detail", "Review Board", "Source Control", "Obsidian", "Calendar", "Morning Standup"} else DEFAULTS["default_page"]
    config["density"] = config["density"] if config["density"] in {"compact", "comfortable"} else DEFAULTS["density"]
    config["task_view"] = config["task_view"] if config.get("task_view") in {"all", "attention", "review", "seen", "my_day", "progress", "today", "overdue", "high", "starred", "none", "resolved"} else DEFAULTS["task_view"]
    for key, minimum, maximum in (("polling_interval_minutes", 1, 120), ("calendar_default_minutes", 5, 480), ("backlog_assignee_id", 0, 2147483647), ("font_size", 9, 14), ("local_llm_timeout_seconds", 30, 1800), ("local_llm_max_tokens", 512, 32768), ("local_llm_context_tokens", 2048, 262144), ("svn_timeout_seconds", 10, 1800)):
        try: config[key] = max(minimum, min(maximum, int(config[key])))
        except (TypeError, ValueError): config[key] = DEFAULTS[key]
    config["track_business_hours_only"] = bool(config.get("track_business_hours_only", True))
    config["work_schedule"] = _work_schedule(data if isinstance(data, dict) else {}).to_config()
    config["general_project"] = str(config.get("general_project") or DEFAULTS["general_project"]).strip()[:60] or DEFAULTS["general_project"]
    config["show_weekends"] = bool(config["show_weekends"])
    config["navigation_collapsed"] = bool(config["navigation_collapsed"])
    config["task_filter_panel"] = bool(config["task_filter_panel"])
    valid_columns = ("priority", "status", "due", "project", "assignee")
    raw_columns = config.get("task_columns")
    config["task_columns"] = [name for name in valid_columns if name in raw_columns] if isinstance(raw_columns, list) else list(DEFAULTS["task_columns"])
    config["custom_launchers"] = [item for item in config["custom_launchers"] if isinstance(item, dict) and item.get("label") and item.get("command")] if isinstance(config["custom_launchers"], list) else []
    saved_views = config.get("saved_task_views")
    config["saved_task_views"] = [
        {
            "name": str(item["name"]).strip()[:100],
            "status": str(item.get("status", "")),
            "project": str(item.get("project", "")),
            "priority": str(item.get("priority", "")),
            "due": str(item.get("due", "all")),
            "view": str(item.get("view", "all")),
        }
        for item in saved_views[:20]
        if isinstance(item, dict) and str(item.get("name", "")).strip()
    ] if isinstance(saved_views, list) else []
    raw_environments = config.get("environment_profiles")
    environments = {}
    if isinstance(raw_environments, dict):
        for project, entries in raw_environments.items():
            if not isinstance(entries, dict):
                continue
            clean_entries = {}
            for environment, profile in entries.items():
                if not isinstance(profile, dict):
                    continue
                clean_entries[str(environment)] = {
                    "url": str(profile.get("url", "")).strip(),
                    "username": str(profile.get("username", "")).strip(),
                }
            if clean_entries:
                environments[str(project)] = clean_entries
    config["environment_profiles"] = environments
    raw_svn_profiles = config.get("svn_profiles")
    svn_profiles = []
    profile_names = set()
    if isinstance(raw_svn_profiles, list):
        for index, profile in enumerate(raw_svn_profiles[:50], 1):
            if not isinstance(profile, dict):
                continue
            root = str(profile.get("root", "")).strip()
            if not root:
                continue
            name = str(profile.get("name") or f"Working copy {index}").strip()[:100]
            normalized_name = name.casefold()
            if normalized_name in profile_names:
                continue
            profile_names.add(normalized_name)
            svn_profiles.append({
                "name": name,
                "root": root,
                "project": str(profile.get("project", "")).strip()[:200],
            })
    config["svn_profiles"] = svn_profiles
    config["svn_show_unversioned"] = bool(config.get("svn_show_unversioned", True))
    config["svn_shelf_all_tickets"] = bool(config.get("svn_shelf_all_tickets", True))
    raw_shelf_sort = config.get("svn_shelf_sort")
    shelf_sort = []
    if isinstance(raw_shelf_sort, list):
        for entry in raw_shelf_sort[:10]:
            if not isinstance(entry, dict) or not str(entry.get("key", "")).strip():
                continue
            try:
                tier = max(0, min(3, int(entry.get("tier", 0))))
                assigned = max(0, min(1000, int(entry.get("assigned", 0))))
            except (TypeError, ValueError):
                continue
            shelf_sort.append({
                "key": str(entry["key"]).strip()[:40],
                "tier": tier,
                "descending": bool(entry.get("descending", False)),
                "assigned": assigned,
            })
    config["svn_shelf_sort"] = shelf_sort
    config["svn_executable"] = str(config.get("svn_executable", "")).strip().strip('"')
    config["tortoise_executable"] = str(config.get("tortoise_executable", "")).strip().strip('"')
    config["svn_patch_root"] = str(config.get("svn_patch_root") or DEFAULTS["svn_patch_root"]).strip().strip('"') or DEFAULTS["svn_patch_root"]
    for key in ("backlog_base_url", "backlog_web_url", "local_llm_url"):
        config[key] = str(config.get(key, DEFAULTS[key])).rstrip("/")
    config["local_llm_thinking"] = bool(config.get("local_llm_thinking", False))
    # The app now speaks the OpenAI-compatible API of a llama.cpp server. An
    # Ollama-era URL or model tag can only fail, so migrate those exact values.
    if str(config.get("local_llm_url", "")).strip() in OLLAMA_URLS:
        config["local_llm_url"] = DEFAULTS["local_llm_url"]
    model = str(config.get("local_llm_model", DEFAULTS["local_llm_model"])).strip()
    if model in OLLAMA_MODEL_TAGS:
        model = ""
    config["local_llm_model"] = model
    for key in ("obsidian_tickets_folder", "obsidian_daily_folder", "obsidian_programming_folder"):
        config[key] = str(config.get(key, DEFAULTS[key])).strip("/\\") or DEFAULTS[key]
    return config

def save_config(config: dict) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    temporary = CONFIG_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(CONFIG_PATH)

def backlog_key() -> str:
    from services.credentials import get_backlog_key
    return get_backlog_key()
