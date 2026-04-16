#!/usr/bin/env python3
"""
Video generation tool using xAI's async video API.
"""

import asyncio
import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional

import httpx

from tools.registry import registry, tool_error
from tools.xai_http import hermes_xai_user_agent

logger = logging.getLogger(__name__)

DEFAULT_XAI_BASE_URL = "https://api.x.ai/v1"
DEFAULT_XAI_VIDEO_MODEL = "grok-imagine-video"
DEFAULT_OPERATION = "generate"
DEFAULT_DURATION = 8
DEFAULT_ASPECT_RATIO = "16:9"
DEFAULT_RESOLUTION = "720p"
DEFAULT_TIMEOUT_SECONDS = 240
DEFAULT_POLL_INTERVAL_SECONDS = 5
VALID_ASPECT_RATIOS = {"1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3"}
VALID_RESOLUTIONS = {"480p", "720p"}
VALID_SIZES = {"848x480", "1696x960", "1280x720", "1920x1080"}
VALID_OPERATIONS = {"generate", "edit", "extend"}


def _get_xai_base_url() -> str:
    return (os.getenv("XAI_BASE_URL") or DEFAULT_XAI_BASE_URL).strip().rstrip("/")


def check_video_generation_requirements() -> bool:
    return bool(os.getenv("XAI_API_KEY", "").strip())


def _xai_headers() -> Dict[str, str]:
    api_key = os.getenv("XAI_API_KEY", "").strip()
    if not api_key:
        raise ValueError("XAI_API_KEY not set. Get one at https://console.x.ai/")
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": hermes_xai_user_agent(),
    }


def _normalize_reference_images(
    image_url: Optional[str],
    reference_image_urls: Optional[List[str]],
) -> tuple[Optional[Dict[str, str]], Optional[List[Dict[str, str]]]]:
    primary_image = None
    if image_url and image_url.strip():
        primary_image = {"url": image_url.strip()}

    refs = []
    for url in reference_image_urls or []:
        normalized = (url or "").strip()
        if normalized:
            refs.append({"url": normalized})
    return primary_image, refs or None


def _normalize_operation(
    operation: Optional[str],
    video_url: Optional[str],
    prompt: Optional[str],
) -> str:
    normalized = (operation or "").strip().lower()
    if not normalized:
        if (video_url or "").strip():
            prompt_lower = (prompt or "").strip().lower()
            if not prompt_lower:
                return "extend"
            extend_cues = (
                "extend",
                "continue",
                "continuation",
                "longer",
                "further",
                "keep going",
                "carry on",
                "more of",
            )
            return "extend" if any(cue in prompt_lower for cue in extend_cues) else "edit"
        return DEFAULT_OPERATION
    aliases = {
        "generate_video": "generate",
        "edit_video": "edit",
        "extend_video": "extend",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in VALID_OPERATIONS:
        raise ValueError(f"operation must be one of {sorted(VALID_OPERATIONS)}")
    return normalized


def _normalize_duration(
    *,
    operation: str,
    duration: Optional[int],
    seconds: Optional[int],
    reference_images_present: bool,
) -> int:
    if operation == "edit":
        # xAI video edits inherit duration from the source video. Ignore any
        # caller-provided duration/seconds instead of rejecting the request.
        return DEFAULT_DURATION

    value = seconds if seconds is not None else duration
    if value is None:
        value = 6 if operation == "extend" else DEFAULT_DURATION

    if value < 1:
        raise ValueError("duration must be at least 1 second")

    if operation == "extend":
        if value > 10:
            raise ValueError("xAI video extension supports a maximum duration of 10 seconds")
    else:
        if value > 15:
            raise ValueError("xAI video generation supports a maximum duration of 15 seconds")
        if reference_images_present and value > 10:
            raise ValueError(
                "xAI video generation supports a maximum duration of 10 seconds when using reference_image_urls"
            )
    return value


async def _submit_video_request(
    client: httpx.AsyncClient,
    operation: str,
    payload: Dict[str, Any],
) -> str:
    endpoint_map = {
        "generate": "videos/generations",
        "edit": "videos/edits",
        "extend": "videos/extensions",
    }
    submit_response = await client.post(
        f"{_get_xai_base_url()}/{endpoint_map[operation]}",
        headers={**_xai_headers(), "x-idempotency-key": str(uuid.uuid4())},
        json=payload,
        timeout=60,
    )
    submit_response.raise_for_status()
    submit_payload = submit_response.json()
    request_id = submit_payload.get("request_id")
    if not request_id:
        raise RuntimeError("xAI video response did not include request_id")
    return request_id


async def video_generate_tool(
    prompt: Optional[str] = None,
    operation: Optional[str] = None,
    duration: Optional[int] = DEFAULT_DURATION,
    seconds: Optional[int] = None,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    resolution: str = DEFAULT_RESOLUTION,
    size: Optional[str] = None,
    video_url: Optional[str] = None,
    image_url: Optional[str] = None,
    reference_image_urls: Optional[List[str]] = None,
    user: Optional[str] = None,
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS,
    prompt_source: Optional[str] = None,
) -> str:
    normalized_prompt = (prompt or "").strip()
    normalized_video_url = (video_url or "").strip() or None
    notes: List[str] = []

    try:
        normalized_operation = _normalize_operation(operation, normalized_video_url, normalized_prompt)
    except ValueError as e:
        return tool_error(str(e))

    normalized_aspect_ratio = (aspect_ratio or DEFAULT_ASPECT_RATIO).strip()
    normalized_resolution = (resolution or DEFAULT_RESOLUTION).strip().lower()
    normalized_size = (size or "").strip()
    normalized_user = (user or "").strip() or None

    if normalized_operation == "extend" and not normalized_prompt:
        normalized_prompt = "Continue the existing video naturally."
        notes.append("used a default continuation prompt because extend was requested without a prompt")
    elif prompt_source == "user_task_fallback" and normalized_prompt:
        notes.append("used the current user message as prompt because the model omitted prompt")
    if normalized_operation == "edit" and not normalized_prompt:
        return tool_error(f"prompt is required for xAI video {normalized_operation}")
    if normalized_operation == "generate" and not normalized_prompt and not (image_url or "").strip():
        return tool_error("prompt is required for text-to-video generation unless image_url is provided")

    if timeout_seconds < 10:
        return tool_error("timeout_seconds must be at least 10")
    if poll_interval_seconds < 1:
        return tool_error("poll_interval_seconds must be at least 1")

    primary_image, refs = _normalize_reference_images(image_url, reference_image_urls)
    if refs and len(refs) > 7:
        return tool_error("reference_image_urls supports at most 7 images with xAI")

    try:
        normalized_duration = _normalize_duration(
            operation=normalized_operation,
            duration=duration,
            seconds=seconds,
            reference_images_present=bool(refs),
        )
    except ValueError as e:
        return tool_error(str(e))

    payload: Dict[str, Any] = {
        "model": DEFAULT_XAI_VIDEO_MODEL,
    }
    if normalized_prompt:
        payload["prompt"] = normalized_prompt
    if normalized_user:
        payload["user"] = normalized_user

    if normalized_operation == "generate":
        if normalized_aspect_ratio not in VALID_ASPECT_RATIOS:
            return tool_error(
                f"aspect_ratio must be one of {sorted(VALID_ASPECT_RATIOS)}"
            )
        if normalized_resolution not in VALID_RESOLUTIONS:
            return tool_error(
                f"resolution must be one of {sorted(VALID_RESOLUTIONS)}"
            )
        if normalized_size and normalized_size not in VALID_SIZES:
            return tool_error(
                f"size must be one of {sorted(VALID_SIZES)}"
            )
        if primary_image and refs:
            return tool_error(
                "image_url and reference_image_urls cannot be combined for xAI video generation"
            )
        payload.update(
            {
                "duration": normalized_duration,
                "aspect_ratio": normalized_aspect_ratio,
                "resolution": normalized_resolution,
            }
        )
        if normalized_size:
            payload["size"] = normalized_size
        if primary_image:
            payload["image"] = primary_image
        if refs:
            payload["reference_images"] = refs

    elif normalized_operation == "edit":
        if not normalized_video_url:
            return tool_error("video_url is required for xAI video edit")
        if primary_image or refs:
            return tool_error("image_url and reference_image_urls are not supported for xAI video edit")
        payload["video"] = {"url": normalized_video_url}
        notes.append("duration, aspect_ratio, and resolution are inherited from the source video for xAI video edit")

    else:
        if not normalized_video_url:
            return tool_error("video_url is required for xAI video extension")
        if primary_image or refs:
            return tool_error("image_url and reference_image_urls are not supported for xAI video extension")
        payload["duration"] = normalized_duration
        payload["video"] = {"url": normalized_video_url}

    try:
        async with httpx.AsyncClient() as client:
            request_id = await _submit_video_request(client, normalized_operation, payload)

            elapsed = 0.0
            last_status = "queued"
            while elapsed < timeout_seconds:
                status_response = await client.get(
                    f"{_get_xai_base_url()}/videos/{request_id}",
                    headers=_xai_headers(),
                    timeout=30,
                )
                status_response.raise_for_status()
                status_payload = status_response.json()
                last_status = (status_payload.get("status") or "").lower()

                if last_status == "done":
                    video = status_payload.get("video") or {}
                    video_url = video.get("url")
                    if not video_url:
                        raise RuntimeError("xAI video generation completed without a video URL")
                    return json.dumps(
                        {
                            "success": True,
                            "provider": "xai",
                            "operation": normalized_operation,
                            "request_id": request_id,
                            "status": "done",
                            "video": video_url,
                            "duration": video.get("duration", normalized_duration),
                            "aspect_ratio": normalized_aspect_ratio if normalized_operation == "generate" else None,
                            "resolution": normalized_resolution if normalized_operation == "generate" else None,
                            "size": normalized_size if normalized_operation == "generate" else None,
                            "respect_moderation": video.get("respect_moderation"),
                            "model": status_payload.get("model"),
                            "usage": status_payload.get("usage"),
                            "notes": notes,
                        },
                        ensure_ascii=False,
                    )

                if last_status in {"failed", "error", "expired", "cancelled"}:
                    error_message = (
                        status_payload.get("error", {}).get("message")
                        or status_payload.get("message")
                        or f"Video generation ended with status '{last_status}'"
                    )
                    return json.dumps(
                        {
                            "success": False,
                            "provider": "xai",
                            "operation": normalized_operation,
                            "request_id": request_id,
                            "status": last_status,
                            "error": error_message,
                        },
                        ensure_ascii=False,
                    )

                await asyncio.sleep(poll_interval_seconds)
                elapsed += poll_interval_seconds

        return json.dumps(
            {
                "success": False,
                "provider": "xai",
                "operation": normalized_operation,
                "request_id": request_id,
                "status": last_status,
                "error": f"Timed out waiting for video generation after {timeout_seconds} seconds",
            },
            ensure_ascii=False,
        )
    except Exception as e:
        logger.error("Video generation failed: %s", e, exc_info=True)
        return json.dumps(
            {
                "success": False,
                "provider": "xai",
                "operation": normalized_operation,
                "error": str(e),
                "error_type": type(e).__name__,
            },
            ensure_ascii=False,
        )


VIDEO_GENERATE_SCHEMA = {
    "name": "video_generate",
    "description": "Generate, edit, or extend short videos with xAI grok-imagine-video. Supports text-to-video, image-to-video, reference-image-guided generation, native video edits, and native video extensions.",
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Describe the video to generate, edit, or extend. Usually pass this whenever the user provides motion, scene, style, edit, or continuation instructions. Optional only for image-to-video calls where the image alone is the complete instruction.",
            },
            "operation": {
                "type": "string",
                "enum": sorted(VALID_OPERATIONS),
                "description": "Video mode. Use 'generate' for new videos, 'edit' to modify an existing video, and 'extend' to continue an existing video.",
                "default": DEFAULT_OPERATION,
            },
            "duration": {
                "type": "integer",
                "description": "Requested duration in seconds. Generate supports 1-15 seconds. Extend supports 1-10 seconds. For xAI video edit, the source video duration is retained.",
                "default": DEFAULT_DURATION,
            },
            "seconds": {
                "type": "integer",
                "description": "Alias for duration for OpenAI-compatible callers.",
            },
            "aspect_ratio": {
                "type": "string",
                "enum": sorted(VALID_ASPECT_RATIOS),
                "description": "Output aspect ratio for generate mode.",
                "default": DEFAULT_ASPECT_RATIO,
            },
            "resolution": {
                "type": "string",
                "enum": sorted(VALID_RESOLUTIONS),
                "description": "Output resolution for generate mode.",
                "default": DEFAULT_RESOLUTION,
            },
            "size": {
                "type": "string",
                "enum": sorted(VALID_SIZES),
                "description": "Optional explicit output size for generate mode.",
            },
            "video_url": {
                "type": "string",
                "description": "Required for edit and extend modes. Source video URL to modify or continue.",
            },
            "image_url": {
                "type": "string",
                "description": "Optional source image URL for image-to-video generation.",
            },
            "reference_image_urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional reference image URLs for generate mode. Use these to carry people, objects, or clothing into a new video without fixing the first frame.",
            },
            "user": {
                "type": "string",
                "description": "Optional end-user identifier forwarded to xAI.",
            },
        },
        "required": ["prompt"],
    },
}


async def _handle_video_generate(args, **kw):
    prompt = args.get("prompt", "")
    prompt_source = None
    if not (prompt or "").strip():
        user_task = kw.get("user_task")
        if user_task and isinstance(user_task, str) and user_task.strip():
            prompt = user_task.strip()
            prompt_source = "user_task_fallback"
            logger.info("video_generate: prompt was empty, falling back to user_task=%r", prompt[:100])
    return await video_generate_tool(
        prompt=prompt,
        operation=args.get("operation"),
        duration=args.get("duration", DEFAULT_DURATION),
        seconds=args.get("seconds"),
        aspect_ratio=args.get("aspect_ratio", DEFAULT_ASPECT_RATIO),
        resolution=args.get("resolution", DEFAULT_RESOLUTION),
        size=args.get("size"),
        video_url=args.get("video_url"),
        image_url=args.get("image_url"),
        reference_image_urls=args.get("reference_image_urls"),
        user=args.get("user"),
        prompt_source=prompt_source,
    )


registry.register(
    name="video_generate",
    toolset="video_gen",
    schema=VIDEO_GENERATE_SCHEMA,
    handler=_handle_video_generate,
    check_fn=check_video_generation_requirements,
    requires_env=["XAI_API_KEY"],
    is_async=True,
    emoji="🎬",
)
