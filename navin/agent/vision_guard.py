"""Say so when a turn carries images the selected model cannot read.

Sending an image to a text-only model has two failure shapes, and the quiet one
is the dangerous one. Some providers reject the request, which
``LLMProvider._retry_without_images`` already recovers from. Many
OpenAI-compatible gateways accept it and simply drop the image block: the model
then answers from the text alone, confidently, with no indication that it never
saw the screenshot the user was asking about.

So the image blocks are removed before the call and replaced by a sentence that
says what happened. The model reads that sentence, cannot hallucinate content it
never received, and can tell the user which model to switch to.
"""

from __future__ import annotations

from pathlib import Path

from navin.providers.model_capabilities import supports_vision

# Kept short: it is prepended to the user's own text, not to a system prompt.
_NOTICE_ONE = (
    "[attachment not delivered: {names} could not be read because the selected "
    "model ({model}) has no image input. Do not describe or guess its content. "
    "Tell the user to switch to a vision model to analyse it.]"
)
_NOTICE_MANY = (
    "[attachments not delivered: {count} images ({names}) could not be read "
    "because the selected model ({model}) has no image input. Do not describe "
    "or guess their content. Tell the user to switch to a vision model to "
    "analyse them.]"
)


def guard_vision_media(
    text: str,
    media: list[str],
    model: str | None,
    *,
    input_modalities: list[str] | None = None,
) -> tuple[str, list[str], bool]:
    """Drop image media a text-only *model* cannot read, and explain the drop.

    Returns the annotated text, the media list to actually send, and whether
    anything was withheld.
    """
    if not media:
        return text, media, False
    if supports_vision(model, input_modalities=input_modalities):
        return text, media, False

    names = [Path(item).name for item in media if isinstance(item, str) and item.strip()]
    if not names:
        return text, media, False

    label = model or "unknown"
    if len(names) == 1:
        notice = _NOTICE_ONE.format(names=names[0], model=label)
    else:
        shown = ", ".join(names[:4])
        if len(names) > 4:
            shown = f"{shown}, +{len(names) - 4} more"
        notice = _NOTICE_MANY.format(count=len(names), names=shown, model=label)

    annotated = f"{text}\n\n{notice}" if text else notice
    return annotated, [], True
