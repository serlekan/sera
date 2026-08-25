# Product: Oryol Calendar

## 1. Overview & Purpose

Oryol Calendar provides unified team scheduling, event management, meeting coordination, and availability booking across Oryol Workspace.

---

## 2. Core Entities

1. **Calendar (`cal_...`)**: Personal or shared team calendar container.
2. **Event (`evt_...`)**: Scheduled meeting with participants, video conference links, attachments, and reminders.
3. **Availability Schedule (`avail_...`)**: Shareable booking link and working-hours rules.
4. **RSVP (`rsvp_...`)**: Participant response state (`accepted`, `declined`, `tentative`).

---

## 3. Architecture Rules & Integrations

- **Centralized Free/Busy**: Team availability is calculated across organization memberships.
- **OryolMail Integration**: Automatic parsing of ICS invite attachments and one-click calendar response handling.
- **Permission Scopes**: `calendar.read`, `calendar.write`, `calendar.share`, `calendar.manage`.
