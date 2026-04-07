from __future__ import annotations

from io import BytesIO


class ImageOCRService:
    """圖片 OCR 服務（使用 RapidOCR）。"""

    def __init__(self, *, max_chars: int = 3000) -> None:
        self.max_chars = max_chars
        self._engine = None

    def _get_engine(self):
        if self._engine is not None:
            return self._engine
        from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-untyped]

        self._engine = RapidOCR()
        return self._engine

    def extract_text(self, image_bytes: bytes) -> str:
        if not image_bytes:
            return ""

        from PIL import Image  # type: ignore[import-untyped]

        image = Image.open(BytesIO(image_bytes)).convert("RGB")
        engine = self._get_engine()
        result, _ = engine(image)
        if not result:
            return ""

        lines: list[str] = []
        for item in result:
            # RapidOCR item format: [box, text, score]
            if len(item) >= 2:
                text = str(item[1]).strip()
                if text:
                    lines.append(text)

        text = "\n".join(lines).strip()
        if len(text) > self.max_chars:
            return text[: self.max_chars]
        return text
