"""Lectura de textos por cámara (punto 5): preparación, OCR y estabilización."""
from vision.ocr.document_scanner import DocumentScanner
from vision.ocr.ocr_engine import OCRBlock, OCREngineChain, OCRResult
from vision.ocr.text_stabilizer import StableText, TextStabilizer, normalize, similarity

__all__ = [
    "DocumentScanner",
    "OCREngineChain", "OCRResult", "OCRBlock",
    "TextStabilizer", "StableText", "normalize", "similarity",
]
