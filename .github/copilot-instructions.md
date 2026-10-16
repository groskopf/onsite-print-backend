# API design reference

The previous version's API is documented at
https://api.printerboks.dk/api/v1/docs.
When implementing or changing the onsite-print API, consult that reference and
replicate its design where it makes sense for this tool. Reuse relevant endpoint
conventions, field names, request/response structures, and behavior rather than
inventing alternatives without checking the reference. Do not blindly copy
legacy behavior or change the current `/api/v2` prefix; explain meaningful
deviations and clarify incompatible requirements with the user.
