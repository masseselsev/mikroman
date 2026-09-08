/**
 * Contract constants shared with the settings API, kept out of `client.js`.
 *
 * Twenty component tests mock `../api/client` wholesale, so anything else
 * imported from that module is undefined under those mocks and the component
 * throws on render. A value that is a *contract* rather than a transport
 * belongs in a module nobody replaces.
 */

/**
 * What `GET /system/settings` returns instead of a stored credential.
 *
 * Mirrors `SECRET_PLACEHOLDER` in `backend/app/api/v1/endpoints/system.py`.
 * The settings form posts the whole object back, so this exact string arriving
 * on `POST /system/settings` means "unchanged" rather than "the operator set
 * the token to eight asterisks" — which is why both sides must agree on it, and
 * why a field showing it is labelled as hidden instead of as filled in.
 */
export const SECRET_PLACEHOLDER = '********';
