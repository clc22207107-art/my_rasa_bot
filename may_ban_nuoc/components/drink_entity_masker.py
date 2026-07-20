"""
DrinkEntityMasker — custom Rasa NLU pre-processor.

Runs BEFORE WhitespaceTokenizer and DIETClassifier.
At training time: replaces drink-name tokens with 'DRINK' placeholder so DIET
  learns intent from sentence STRUCTURE, not from which brand appears.
At inference time: same masking → DrinkEntityMasker is the authoritative
  extractor for drink entities; non-drink entities (size, quantity, payment…)
  are still extracted by DIET's entity recognition.

Satisfies the three project constraints:
  1. Pure intent-classification + slot-filling (no keyword matching for intent)
  2. Train/test structural diversity guaranteed — intent learned from 'DRINK'
  3. Stateless; runs offline on Pi 5 with zero extra model RAM
"""

import re
import logging
from typing import Any, Dict, List, Optional, Text, Tuple

from rasa.engine.graph import GraphComponent, ExecutionContext
from rasa.engine.recipes.default_recipe import DefaultV1Recipe
from rasa.engine.storage.resource import Resource
from rasa.engine.storage.storage import ModelStorage
from rasa.shared.nlu.training_data.message import Message
from rasa.shared.nlu.training_data.training_data import TrainingData
from rasa.shared.nlu.constants import TEXT, ENTITIES

logger = logging.getLogger(__name__)

DRINK_PLACEHOLDER = "DRINK"

_WORD_NUMBERS = {
    "ten": "10", "nine": "9", "eight": "8", "seven": "7", "six": "6",
    "five": "5", "four": "4", "three": "3", "two": "2", "one": "1",
}

def _normalize_numbers(text: str) -> str:
    for word, digit in _WORD_NUMBERS.items():
        text = re.sub(rf"(?<!\w){word}(?!\w)", digit, text, flags=re.IGNORECASE)
    return text

# ---------------------------------------------------------------------------
# Comprehensive alias table — built from vending_machine.db + nlu.yml synonyms.
# Sort order does NOT matter here; the regex engine sorts by length at build time.
# ---------------------------------------------------------------------------
_DRINK_ALIASES: List[str] = [
    # coca-cola
    "coca-cola", "coca cola", "cocacola", "coke", "coco", "coca",
    # pepsi
    "pepsi cola", "pepsi-cola", "pepsi",
    # red bull
    "red bull energy", "redbull energy", "red-bull energy",
    "red bull", "red-bull", "redbull",
    # 7up
    "seven up", "sevenup", "7 up", "7up",
    # sprite
    "sprite soda", "lemon sprite", "sprite",
    # monster
    "monster energy", "monster drink", "monster",
    # fanta
    "fanta orange", "fanta grape", "fanta",
    # mirinda
    "mirinda orange", "mirinda",
    # aquafina
    "aquafina water", "aquafina",
    # lavie
    "la vie water", "la vie", "lavie",
    # revive
    "revive electrolyte", "revive",
    # c2
    "c2 green tea", "c2 lemon", "c2",
    # sting
    "sting energy drink", "sting energy", "sting",
    # yakult
    "yakult",
    # vita milk / soy milk vita
    "soy milk vita", "vita soy milk", "vita-milk", "vita milk", "vitamilk",
    # warrior
    "warrior",
    # wakeup 247
    "wake up 247", "wakeup 247", "wake up", "wakeup247", "wakeup",
    # cocoxim — "coconut water cocoxim" first to avoid short alias grabbing unrelated text
    "coconut water cocoxim", "cocoxim coconut", "coco xim", "cocoxim",
    # lipton
    "lipton ice tea", "lipton green tea", "lipton peach tea", "lipton tea", "lipton",
    # nestea
    "nestea peach tea", "nestea tea", "nestea",
    # birdy
    "birdy canned coffee", "birdy coffee", "cafe birdy", "birdy",
    # nescafe
    "nescafe coffee", "nescafe",
    # zero degree green tea
    "zero degree green tea", "0 degree green tea", "zero degree", "0 degree", "zerodegree",
    # oolong tea (including STT variants from nlu.yml synonym)
    "olong tea plus", "oolong tea", "olong tea", "o long tea", "oolong", "olong", "o long",
    # brown rice tea
    "roasted brown rice tea", "roasted rice tea", "brown rice tea", "browntea", "rice tea",
    # dr thanh herbal
    "herbal drink dr thanh", "doctor thanh", "dr. thanh", "dr thanh", "drthanh",
    # number 1
    "number one", "number 1", "num 1", "number1", "no 1", "no1",
    # aloe vera
    "aloe vera drink", "aloe vera",
    # twister
    "twister orange juice", "twister orange", "twister",
    # dutch lady
    "dutch lady milk", "dutchlady", "dutch lady",
    # th true milk
    "th truemilk", "th true milk", "th milk",
    # vinamilk
    "vinamilk chocolate", "vinamilk milk", "vinamilk",
]


def _build_pattern() -> re.Pattern:
    """Compile a single regex that matches any drink alias (longest first)."""
    sorted_aliases = sorted(_DRINK_ALIASES, key=len, reverse=True)
    escaped = [re.escape(a) for a in sorted_aliases]
    # \b word-boundary; s? optional trailing plural 's'
    return re.compile(
        r"(?<!\w)(" + "|".join(escaped) + r")s?(?!\w)",
        re.IGNORECASE,
    )


@DefaultV1Recipe.register(
    DefaultV1Recipe.ComponentType.MESSAGE_TOKENIZER, is_trainable=False
)
class DrinkEntityMasker(GraphComponent):
    """
    Pre-processing component: masks drink names with 'DRINK' before DIET sees
    the text, and injects drink entity annotations extracted by alias lookup.
    """

    def __init__(self, config: Optional[Dict[Text, Any]] = None) -> None:
        self._config = config or {}
        self._pattern: re.Pattern = _build_pattern()

    @classmethod
    def create(
        cls,
        config: Dict[Text, Any],
        model_storage: ModelStorage,
        resource: Resource,
        execution_context: ExecutionContext,
    ) -> "DrinkEntityMasker":
        return cls(config)

    @staticmethod
    def get_default_config() -> Dict[Text, Any]:
        return {}

    # ------------------------------------------------------------------
    # Core masking logic
    # ------------------------------------------------------------------

    def _mask_message(self, message: Message) -> None:
        """Mask drink names in a single message and inject drink entities."""
        text = message.get(TEXT) or ""
        if not text:
            return

        existing_entities: List[Dict] = list(message.get(ENTITIES) or [])

        # Separate existing drink vs non-drink entity annotations
        annot_drinks = [e for e in existing_entities if e.get("entity") == "drink"]
        non_drink_ents = [e for e in existing_entities if e.get("entity") != "drink"]

        # Build replacement spans from annotations (training) or regex (inference)
        replacements: List[Tuple[int, int, str]] = []

        if annot_drinks:
            # Training path: use entity annotation positions (more accurate)
            for e in sorted(annot_drinks, key=lambda x: x.get("start", 0)):
                start = e.get("start", 0)
                end = e.get("end", start)
                value = e.get("value") or text[start:end]
                replacements.append((start, end, value))
        else:
            # Inference path: detect drink names via regex
            for m in self._pattern.finditer(text):
                replacements.append((m.start(), m.end(), m.group(1).lower()))

        if not replacements:
            return

        # Build masked text and compute cumulative position offsets
        parts: List[str] = []
        prev_end = 0
        cumulative = 0  # running offset: sum of (len(DRINK) - len(original))
        # offset_breakpoints[i] = (original_char_end_after_replacement_i, cumulative_offset)
        offset_breakpoints: List[Tuple[int, int]] = []
        new_drink_ents: List[Dict] = []

        for start, end, value in replacements:
            parts.append(text[prev_end:start])
            parts.append(DRINK_PLACEHOLDER)
            delta = len(DRINK_PLACEHOLDER) - (end - start)
            new_start = start + cumulative
            new_end = new_start + len(DRINK_PLACEHOLDER)
            new_drink_ents.append({
                "entity": "drink",
                "value": value,
                "start": new_start,
                "end": new_end,
                "confidence": 1.0,
                "extractor": "DrinkEntityMasker",
            })
            cumulative += delta
            offset_breakpoints.append((end, cumulative))
            prev_end = end

        parts.append(text[prev_end:])
        masked_text = "".join(parts)

        # Inference only: normalize word numbers so DIET sees digits ("3") not words ("three")
        if not annot_drinks:
            masked_text = _normalize_numbers(masked_text)

        def offset_at(char_pos: int) -> int:
            """Cumulative character offset at a given position in the original text."""
            result = 0
            for bp_end, bp_offset in offset_breakpoints:
                if bp_end <= char_pos:
                    result = bp_offset
                else:
                    break
            return result

        # Update positions of non-drink entity annotations
        updated_non_drink: List[Dict] = []
        for e in non_drink_ents:
            s = e.get("start", 0)
            end_e = e.get("end", 0)
            updated_non_drink.append({
                **e,
                "start": s + offset_at(s),
                "end": end_e + offset_at(end_e),
            })

        message.set(TEXT, masked_text)

        if annot_drinks:
            # Training path: only expose non-drink entities so DIET learns to tag
            # "DRINK" tokens as O (outside). DrinkEntityMasker is the sole extractor
            # for drinks; letting DIET also extract them causes "DRINK" → synonym bugs.
            message.set(ENTITIES, updated_non_drink, add_to_output=True)
            logger.debug(
                f"[train] '{text}' → '{masked_text}' | drinks removed from annotations"
            )
        else:
            # Inference path: expose drink entities so downstream actions can use them.
            message.set(ENTITIES, updated_non_drink + new_drink_ents, add_to_output=True)

    # ------------------------------------------------------------------
    # Rasa 3.x GraphComponent interface
    # ------------------------------------------------------------------

    def process_training_data(self, training_data: TrainingData) -> TrainingData:
        """Mask drink names in all training examples (called during rasa train)."""
        for message in training_data.training_examples:
            self._mask_message(message)
        return training_data

    def process(self, messages: List[Message]) -> List[Message]:
        """Mask drink names in live messages (called during rasa run / test)."""
        for message in messages:
            self._mask_message(message)
        return messages
