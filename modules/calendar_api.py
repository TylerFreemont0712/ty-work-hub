"""Google Calendar adapter. Optional until OAuth credentials are configured."""
from datetime import datetime, timedelta
from modules.ticket_utils import issue_key

class CalendarClient:
    def __init__(self, service=None): self.service = service
    def create_work_event(self, ticket: dict, minutes: int = 30) -> dict:
        if not self.service:
            return {"id": "local", "summary": f"Work: {issue_key(ticket)} {ticket['summary']}"}
        start = datetime.now().astimezone(); end = start + timedelta(minutes=minutes)
        body = {"summary": f"Work: {issue_key(ticket)} {ticket['summary']}", "description": ticket.get("description", ""), "start": {"dateTime": start.isoformat()}, "end": {"dateTime": end.isoformat()}, "colorId": "2"}
        return self.service.events().insert(calendarId="primary", body=body).execute()
