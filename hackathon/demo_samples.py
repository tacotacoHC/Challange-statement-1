"""Fictional, source-bound examples for the local SiteBrief demonstration."""
from datetime import date, timedelta


def get_demo_data(today=None):
    today = date.fromisoformat(str(today)) if today else date.today()
    tomorrow = (today + timedelta(days=1)).isoformat()
    yesterday = (today - timedelta(days=1)).isoformat()
    day = today.isoformat()
    project = {
        "id": "project-riverside", "name": "Riverside Residences", "code": "RR-2026",
        "location": "18 Riverside Drive, Singapore", "client": "Riverside Development Pte Ltd",
        "manager": "Alex Morgan", "status": "Active", "description": "Fictional 12-storey residential development",
        "targetDate": (today + timedelta(days=120)).isoformat(),
        "contractors": ["Apex Civil", "Meridian M&E", "Clearline Facades"],
    }
    def update(key, time, contributor, location, discipline, text, kind="note", **extra):
        return {"id": f"demo-{key}", "projectId": project["id"], "date": day, "time": time,
                "contributor": contributor, "location": location, "discipline": discipline,
                "type": kind, "text": text, "caption": "", "transcript": "", "files": [],
                "demo": True, "processing": "Demo source — no image or audio analysis performed", **extra}
    updates = [
        update("slab", "07:45", "Daniel Lee", "Block A · Level 3", "Structural",
            "Level 3 slab pour completed at 07:30. 42 m³ concrete placed. Curing blankets are in place. Inspection sign-off has not yet been recorded."),
        update("wet-area", "08:10", "Priya Nair", "Block A · Level 2 wet areas", "Indoor / wet spaces",
            "Waterproofing installation is ongoing in the Level 2 bathrooms. Eight of twelve units have received the first coat. Flood testing has not started."),
        update("steel-delay", "08:35", "Mei Tan", "Loading bay · Block B", "Structural",
            f"Rebar delivery is delayed by two days; supplier now expects arrival on {tomorrow}. Block B reinforcement work is blocked pending delivery. Owner: Mei Tan; deadline: {tomorrow}. Mei to confirm revised delivery slot with supplier.", "chat"),
        update("pump", "09:05", "Omar Hassan", "Basement · Pump room", "M&E",
            "Dewatering pump P-02 is not starting. Water is accumulating near the sump. Please arrange inspection. No owner or target date has been agreed.", "chat"),
        update("edge", "09:20", "Sarah Lim", "Block A · Level 4 east edge", "Safety",
            "URGENT: missing edge protection reported at the east slab edge on Level 4. Keep personnel away from the affected area and request an immediate safety officer assessment. This report has not been independently verified."),
        update("facade-clear", "09:40", "Ethan Chen", "Block A · East facade", "Facade",
            "East facade access is clear. Scaffold crew can proceed after the toolbox meeting.", "chat"),
        update("facade-blocked", "10:05", "Nora Aziz", "Block A · East facade", "Facade",
            "East facade access is blocked by stored pallets. Scaffold crew cannot proceed until access is cleared.", "chat"),
        update("survey", "10:25", "Lucas Wong", "Block B · Grid C4", "Coordination",
            f"Setting-out survey is awaiting coordination approval. Owner: Lucas Wong; deadline: {yesterday}. Lucas to obtain approval from the design coordinator. No completion has been reported."),
        update("photo", "10:40", "Priya Nair", "Block A · Level 2 wet areas", "Indoor / wet spaces", "", "photo",
            caption="Reporter caption: first waterproofing coat at Level 2 bathroom, unit 02-08. Requires inspector review before the next coat. The image is a fictional diagram, not site photography.",
            files=[{"id": "demo-photo", "name": "wet-area-reference.svg", "mime": "image/svg+xml", "url": "/demo/evidence.svg", "size": 0}],
            processing="Fictional demo illustration; no image analysis performed"),
    ]
    return {"project": project, "projects": [project], "updates": updates}


def demo_samples(today=None):
    return get_demo_data(today)
