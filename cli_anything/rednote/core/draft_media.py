"""Bind draft image references to their original location before later publishing."""

from pathlib import Path, PurePosixPath, PureWindowsPath

from .errors import RedNoteError


def _is_url(value: str) -> bool:
    return value.lower().startswith(("http://", "https://"))


def _absolute(value: str) -> bool:
    return PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute()


def bind_draft_images(images: list[str] | tuple[str, ...]) -> list[str]:
    """Save references only, without requiring files to exist yet.

    Preserve URLs and absolute backend paths (even for another OS). Relative
    local references are bound to the directory where the user supplied them.
    """
    result = []
    for image in images:
        image = image.strip()
        if image and not _is_url(image) and not _absolute(image):
            image = str(Path(image).expanduser().resolve())
        result.append(image)
    return result


def require_bound_draft_images(images: list[str]) -> list[str]:
    """Do not retarget older or library-created relative references at publish."""
    if any(not _is_url(image) and not _absolute(image) for image in images):
        raise RedNoteError(
            "AMBIGUOUS_DRAFT_MEDIA",
            "草稿包含未记录来源目录的相对图片路径。",
            "请从图片所在目录使用 draft update ID --image PATH 重新指定图片，或提供绝对路径，然后重新预览发布。",
        )
    return images
