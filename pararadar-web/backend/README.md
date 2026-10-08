# ParaRadar TikTok API backend (development starter)

This directory is **separate from the existing Render static site** and does not change its routes or appearance.

## Current features
- FastAPI health endpoint.
- TikTok OAuth authorization URL with CSRF state validation.
- Authorization-code token exchange. Tokens are deliberately not stored or exposed.

## Not implemented
- Real user sessions, encrypted token persistence and refresh.
- Content Posting API upload/publish workflow and publishing status.
- TikTok sandbox end-to-end demo and app review submission.

## Development
Install dependencies from requirements.txt and run `uvicorn app:app --reload` from this directory.
Configure the environment variables in `.env.example` **in your backend host**, not in GitHub or browser code.
Register the exact callback URI with TikTok Developer Portal and request the required scopes.
The backend must be deployed to a HTTPS host before browser OAuth works.

**Never commit TikTok client secrets or access tokens.** App review requires a real integrated workflow; this scaffold is not a valid demo by itself.
