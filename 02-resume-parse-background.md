Task 2 — Fix resume-parse 502 by converting to a background job
The resume-parse endpoint currently times out / returns 502 on Render because parsing can take longer than the request window. Apply the same background-job pattern that fixed ingestion.

Convert the resume-parse endpoint to return a job/run id immediately (HTTP 202) and process the parsing in a background task. Use the same background-job infrastructure already in place for ingestion — do not introduce a new pattern.
Store parse status (pending / running / success / error) and the result (or error message) keyed by the run id, so the frontend can poll.
Update the frontend resume-upload flow: after upload, poll the status endpoint at a reasonable interval (e.g. every 2 seconds, with a max wait), show a "parsing your resume…" indicator, then surface the parsed result when ready or the error if it fails.
Handle a failed parse cleanly on the frontend (show the actual error message, allow retry) instead of crashing.
Keep all other resume/profile behavior intact — only the parse step changes shape.

Open a PR base: main, merge main first if conflicts, summarize, then stop.
