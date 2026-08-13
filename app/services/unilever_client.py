"""HTTP client for the Unilever study engine (generate / status / launch)."""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppError

logger = logging.getLogger(__name__)


class UnileverClient:
    def __init__(self, *, access_token: str, timeout: float = 60.0) -> None:
        settings = get_settings()
        base = (settings.UNILEVER_API_BASE_URL or "").rstrip("/")
        if not base:
            raise AppError(
                "Study generation isn’t configured yet. Set UNILEVER_API_BASE_URL.",
                status_code=503,
            )
        self.base = base
        self.access_token = access_token
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    def websocket_base(self) -> str:
        settings = get_settings()
        if settings.UNILEVER_WS_BASE_URL:
            return settings.UNILEVER_WS_BASE_URL.rstrip("/")
        # http(s)://host → ws(s)://host
        if self.base.startswith("https://"):
            return "wss://" + self.base[len("https://") :]
        if self.base.startswith("http://"):
            return "ws://" + self.base[len("http://") :]
        return self.base

    def job_websocket_url(self, job_id: str) -> str:
        # Token is attached by the frontend at connect time — never store it here.
        return f"{self.websocket_base()}/api/v1/ws/task-generation/{job_id}"

    def simulate_websocket_url(self, job_id: str) -> str:
        return f"{self.websocket_base()}/api/v1/ws/simulate-ai/{job_id}"

    def generate_tasks(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v1/studies/generate-tasks", json=payload)

    def job_status(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/studies/generate-tasks/status/{job_id}")

    def job_result(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/studies/generate-tasks/result/{job_id}")

    def launch_study(self, study_id: UUID, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return self._request(
            "PUT",
            f"/api/v1/studies/{study_id}/launch",
            json=body or {},
        )

    def get_study_preview(self, study_id: UUID) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/studies/{study_id}/preview")

    def start_simulate_ai_respondents(
        self,
        study_id: UUID,
        *,
        randomize: bool = False,
        max_respondents: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"randomize": randomize}
        if max_respondents is not None and max_respondents >= 1:
            body["max_respondents"] = max_respondents
        return self._request(
            "POST",
            f"/api/v1/studies/{study_id}/simulate-ai-respondents",
            json=body,
        )

    def simulate_job_status(self, job_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/v1/studies/simulate-ai-respondents/status/{job_id}",
        )

    def get_study_analytics(self, study_id: UUID) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/api/v1/responses/analytics/study/{study_id}",
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base}{path}"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.request(
                    method,
                    url,
                    headers=self._headers(),
                    json=json,
                )
        except httpx.TimeoutException as exc:
            logger.warning("Unilever API timeout: %s %s", method, path)
            raise AppError(
                "The study engine took too long to respond. Please try again.",
                status_code=504,
            ) from exc
        except httpx.HTTPError as exc:
            logger.exception("Unilever API connection failed: %s %s", method, path)
            raise AppError(
                "We couldn’t reach the study engine. Please try again shortly.",
                status_code=502,
            ) from exc

        if response.status_code >= 400:
            detail = _extract_detail(response)
            logger.warning(
                "Unilever API error %s %s → %s: %s",
                method,
                path,
                response.status_code,
                detail[:300],
            )
            mapped = 502
            if response.status_code in {401, 403}:
                mapped = 403
            elif response.status_code == 404:
                mapped = 404
            elif response.status_code == 422:
                mapped = 422
            elif 400 <= response.status_code < 500:
                mapped = 400
            raise AppError(detail, status_code=mapped)

        if not response.content:
            return {}
        try:
            data = response.json()
        except ValueError as exc:
            raise AppError(
                "The study engine returned an unexpected response.",
                status_code=502,
            ) from exc
        if not isinstance(data, dict):
            raise AppError(
                "The study engine returned an unexpected response.",
                status_code=502,
            )
        return data


def _extract_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        text = (response.text or "").strip()
        return text[:300] or "The study engine rejected the request."
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()
        if isinstance(detail, list) and detail:
            first = detail[0]
            if isinstance(first, dict) and first.get("msg"):
                return str(first["msg"])
        message = body.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return "The study engine rejected the request."
