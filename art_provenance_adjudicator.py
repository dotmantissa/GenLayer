# v0.1.0
# { "Depends": "py-genlayer:latest" }

from genlayer import *
import json

ERROR_EXPECTED = "[EXPECTED]"
ERROR_EXTERNAL = "[EXTERNAL]"


def _raise_user_error(message: str) -> None:
    err_cls = getattr(gl, "UserError", Exception)
    raise err_cls(message)


def _run_prompt_consensus(fn, principle: str) -> str:
    eq = getattr(gl, "eq_principle", None)
    if eq is not None and hasattr(eq, "prompt_comparative"):
        return eq.prompt_comparative(fn, principle)
    return fn()


class ArtProvenanceAdjudicator(gl.Contract):
    """Assesses artwork provenance chain quality and authenticity risk from public sources."""

    dossiers: str
    next_dossier_id: u256

    def __init__(self):
        """Initialize contract state.

        Parameters:
            None.

        Returns:
            None.
        """
        self.dossiers = "{}"
        self.next_dossier_id = 1

    @gl.public.write
    def create_dossier(self, artwork_id: str, artist_name: str, min_chain_steps: int) -> str:
        """Create provenance dossier request.

        Parameters:
            artwork_id: External artwork identifier.
            artist_name: Claimed artist name.
            min_chain_steps: Minimum provenance steps required.

        Returns:
            Dossier id string.
        """
        aid = str(artwork_id).strip()
        artist = str(artist_name).strip()

        if len(aid) < 3:
            _raise_user_error(f"{ERROR_EXPECTED} invalid artwork_id")
        if len(artist) < 3:
            _raise_user_error(f"{ERROR_EXPECTED} invalid artist_name")
        if min_chain_steps < 1 or min_chain_steps > 30:
            _raise_user_error(f"{ERROR_EXPECTED} min_chain_steps out of range")

        dossier_id = str(self.next_dossier_id)
        self.next_dossier_id += 1

        dossiers = json.loads(self.dossiers)
        dossiers[dossier_id] = {
            "dossier_id": dossier_id,
            "requester": str(gl.message.sender_account),
            "artwork_id": aid,
            "artist_name": artist,
            "min_chain_steps": int(min_chain_steps),
            "status": "PENDING",
            "provenance_steps": 0,
            "completeness_pct": 0,
            "authenticity_verdict": "UNKNOWN",
            "reason": "",
            "resolved_at": "",
        }
        self.dossiers = json.dumps(dossiers)
        return dossier_id

    @gl.public.write
    def assess_dossier(self, dossier_id: str) -> str:
        """Assess provenance and authenticity claim for an artwork dossier.

        Parameters:
            dossier_id: Dossier id string.

        Returns:
            Authenticity verdict string.
        """
        dossiers = json.loads(self.dossiers)
        key = str(dossier_id)
        if key not in dossiers:
            _raise_user_error(f"{ERROR_EXPECTED} dossier not found")

        d = dossiers[key]
        if d["status"] != "PENDING":
            _raise_user_error(f"{ERROR_EXPECTED} dossier already assessed")

        def fetch_and_assess() -> str:
            artsy_url = f"https://api.artsy.net/api/artworks/{d['artwork_id']}"
            mutualart_url = f"https://www.mutualart.com/api/artworks/{d['artwork_id']}/provenance"

            artsy = gl.nondet.web.get(artsy_url)
            mutual = gl.nondet.web.get(mutualart_url)

            for name, res in [("artsy", artsy), ("mutualart", mutual)]:
                status = int(res.status)
                if status >= 400 and status < 500:
                    _raise_user_error(f"{ERROR_EXTERNAL} {name} client error: {status}")
                if status >= 500:
                    _raise_user_error(f"{ERROR_EXTERNAL} {name} server error: {status}")

            artsy_body = artsy.body.decode("utf-8") if artsy.body is not None else ""
            mutual_body = mutual.body.decode("utf-8") if mutual.body is not None else ""
            if len((artsy_body + mutual_body).strip()) == 0:
                _raise_user_error(f"{ERROR_EXTERNAL} empty provenance payload")

            prompt = f"""
You are a provenance and authenticity specialist.
Read multilingual provenance records and evaluate chain completeness and authenticity confidence.
Return JSON only.

Artwork id: {d['artwork_id']}
Claimed artist: {d['artist_name']}
Minimum chain steps: {d['min_chain_steps']}

Rules:
1) Infer number of provenance ownership transitions from both sources.
2) Compute completeness_pct from 0 to 100 for documentation strength.
3) Authenticity verdict must be AUTHENTIC, UNCERTAIN, or HIGH_RISK.
4) If chain steps are below minimum, downgrade verdict to at most UNCERTAIN.

Return exactly:
{{
  "provenance_steps": int,
  "completeness_pct": int,
  "authenticity_verdict": "AUTHENTIC_or_UNCERTAIN_or_HIGH_RISK",
  "reason": "string"
}}

Inputs:
{json.dumps({"artsy": artsy_body[:6000], "mutualart": mutual_body[:6000]})}
"""
            raw = gl.nondet.exec_prompt(prompt)
            if isinstance(raw, dict):
                parsed = raw
            else:
                cleaned = str(raw).replace("```json", "").replace("```", "").strip()
                parsed = json.loads(cleaned)

            steps = int(parsed.get("provenance_steps", 0))
            if steps < 0:
                steps = 0
            completeness = int(parsed.get("completeness_pct", 0))
            if completeness < 0:
                completeness = 0
            if completeness > 100:
                completeness = 100

            verdict = str(parsed.get("authenticity_verdict", "UNCERTAIN")).strip().upper()
            if verdict not in ["AUTHENTIC", "UNCERTAIN", "HIGH_RISK"]:
                verdict = "UNCERTAIN"

            if steps < int(d["min_chain_steps"]) and verdict == "AUTHENTIC":
                verdict = "UNCERTAIN"

            reason = str(parsed.get("reason", ""))[:500]

            return json.dumps(
                {
                    "provenance_steps": steps,
                    "completeness_pct": completeness,
                    "authenticity_verdict": verdict,
                    "reason": reason,
                }
            )

        principle = "Equivalent when verdict matches and completeness differs by at most 15 points."
        verdict_json = _run_prompt_consensus(fetch_and_assess, principle)
        verdict = json.loads(verdict_json)

        d["provenance_steps"] = int(verdict.get("provenance_steps", 0))
        d["completeness_pct"] = int(verdict.get("completeness_pct", 0))
        d["authenticity_verdict"] = str(verdict.get("authenticity_verdict", "UNCERTAIN"))
        d["reason"] = str(verdict.get("reason", ""))
        d["status"] = "ASSESSED"
        d["resolved_at"] = str(gl.block.timestamp)

        dossiers[key] = d
        self.dossiers = json.dumps(dossiers)

        return d["authenticity_verdict"]

    @gl.public.view
    def get_dossier(self, dossier_id: str) -> str:
        """Read one dossier.

        Parameters:
            dossier_id: Dossier id string.

        Returns:
            Dossier JSON string.
        """
        dossiers = json.loads(self.dossiers)
        key = str(dossier_id)
        if key not in dossiers:
            _raise_user_error(f"{ERROR_EXPECTED} dossier not found")
        return json.dumps(dossiers[key])

    @gl.public.view
    def get_all_dossiers(self) -> str:
        """Read all dossiers.

        Parameters:
            None.

        Returns:
            Dossiers map JSON string.
        """
        return self.dossiers
