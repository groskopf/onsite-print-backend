# API security plan

These notes describe the proposed access-control design for the onsite-print
backend. They are a plan, not implemented security guarantees. At the time of
writing, the registered API routes have no application-level authentication or
authorization.

Keep the `/api/v2` prefix. Enforce permissions in the backend, regardless of
which webpage or device sends a request.

## Identities and access levels

Use separate identities for printers and people.

| Identity | Allowed access | Scope |
|----------|----------------|-------|
| Printer | Find its current booking, list/download nametags, and report printing results if that workflow is added | Only bookings assigned to that printer |
| Event staff | Manage attendees and generate nametags; access event resources | Only explicitly assigned events |
| Administrator | Manage events, bookings, printers, users, and permissions | Entire system |

Event-staff access can be deferred if all initial administration users need full
access. Attendee management and printing-result reporting describe intended
permissions, not necessarily existing endpoints.

A role check alone is insufficient. Every request must also check access to the
specific resource. Changing a booking code in a URL must not let a printer read
another printer's nametags or let staff access another event.

Event, booking, and printer codes are identifiers, not passwords.

## Printer authentication

Enroll each printer with its own cryptographically random secret, sent over
HTTPS in a header:

```http
Authorization: Bearer <device-token>
```

Start with opaque, database-backed tokens; JWTs are not required for printers.
Store a token hash rather than the raw secret, associated with:

- The printer identity.
- Enabled/revoked status.
- Creation and expiry information.
- Last-used time.

Store the secret on the printer in a restricted local configuration file.
Define a secure enrollment and credential-delivery process before implementation.

Individual credentials allow:

- Revoking one lost or compromised printer without disrupting the others.
- Rotating credentials without changing printer or booking codes.
- Tracing access to a particular device identity.
- Restricting printers without granting event or booking administration rights.

A copied token can impersonate the enrolled printer. If stronger physical-device
assurance becomes necessary, consider client certificates/mTLS, accepting the
additional provisioning and deployment complexity.

## Browser authentication

Authenticate the person, not the webpage. Never embed a shared API secret in
frontend JavaScript: browser users can extract it.

The proposed browser flow is:

1. The user signs in.
2. The backend establishes a server-side session.
3. Each API request checks the session, user permissions, and resource access.

For the existing same-origin nginx arrangement, use a session cookie with
`Secure`, `HttpOnly`, and an appropriate `SameSite` policy, plus CSRF protection
for state-changing requests. Define session expiry, logout, and revocation
behavior as part of implementation.

Prefer an established OpenID Connect identity provider, if available, rather
than building password recovery, MFA, and account security ourselves. The
application must still control which signed-in users receive which permissions;
successful login alone does not grant administration access.

CORS, hidden UI controls, and request-origin checks are not authorization and
must not replace backend permission checks.

## Backend enforcement

Use shared FastAPI authentication and authorization dependencies. Deny access by
default and cover every relevant endpoint, including file downloads.

Initial permission rules:

- Event creation and deletion require an administrator.
- Booking management requires an administrator initially.
- Event-scoped operations require an administrator or staff assigned to that
  event, with permissions appropriate to the operation.
- Nametag downloads require authorized staff or the printer assigned to the
  booking.
- A printer's current-booking lookup derives the printer identity from its
  credential rather than trusting a caller-supplied printer code.
- Printer credentials cannot create events or edit bookings.

The existing `require_booking` dependency in
[endpoint_nametags.py](../app/endpoint_nametags.py) checks that a booking belongs
to an event, but does not authenticate the caller. Integrate resource-level
authorization with this validation rather than treating booking existence as
permission.

Decide explicitly whether printers may delete nametags or acknowledge printing.
Do not automatically grant them every nametag operation. Also define whether
expired bookings remain accessible to printers, separately from the existing
active-booking restriction on nametag generation.

## Supporting safeguards

- Require HTTPS for browser and printer traffic.
- Keep the backend and database off direct public access where practical.
- Never place credentials in URLs or logs.
- Audit administrative changes and credential enrollment, rotation, and
  revocation without recording secrets.
- Keep any public health response minimal.
- Decide who may access API documentation and the OpenAPI schema.

Test both authentication and resource authorization, including:

- Missing, invalid, expired, and revoked credentials.
- Authenticated callers with insufficient permissions.
- Cross-printer and cross-event access attempts.
- List endpoints returning only authorized resources.
- File downloads and destructive operations.
- Browser session expiry, logout, and CSRF protection.

## Legacy API reference

The [legacy API documentation](https://api.printerboks.dk/api/v1/docs) declares
an `access_token` API key accepted in either a header or a query parameter.

Preserve the general device-key approach where useful, but deliberately avoid
query-string credentials because URLs commonly appear in logs and browser
history. The proposed header is `Authorization: Bearer <device-token>`.
Existing clients would need a coordinated authentication update; this is not
transparent compatibility with the legacy credential transport.

## Proposed implementation order

1. Agree on the permission matrix and secure printer enrollment process.
2. Add individual printer credentials and restricted printer access.
3. Add administrator login and server-side browser sessions.
4. Protect every existing endpoint and file download before exposing the system
   as secured.
5. Add event-specific staff access if needed.

Outstanding decisions include the identity provider, initial administrator
provisioning, device-token lifetime and rotation policy, session lifetime, and
printer deletion/acknowledgement permissions.
