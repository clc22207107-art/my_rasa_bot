"""Assertions against the actual conversation tracker, not a second NLU request."""
import json
import re


def edit_distance(a, b):
    row = list(range(len(b)+1))
    for i, x in enumerate(a, 1):
        new = [i]
        for j, y in enumerate(b, 1):
            new.append(min(new[-1]+1, row[j]+1, row[j-1]+(x != y)))
        row = new
    return row[-1]


def normalized(text):
    return " ".join(re.findall(r"\w+", text.lower()))


def speech_scores(reference, hypothesis):
    ref, hyp = normalized(reference), normalized(hypothesis)
    return dict(wer=edit_distance(ref.split(), hyp.split())/max(1, len(ref.split())),
                cer=edit_distance(ref, hyp)/max(1, len(ref)))


def evaluate(turn, tracker, responses, context):
    checks = []
    def check(name, actual, expected):
        checks.append(dict(name=name, actual=actual, expected=expected, passed=actual == expected))

    latest = tracker.get("latest_message", {})
    check("tracker.turn_id", latest.get("metadata", {}).get("turn_id"), context["turn_id"])
    check("tracker.session_id", tracker.get("sender_id"), context["session_id"])
    expected = turn["expect"]
    check("intent", latest.get("intent", {}).get("name"), expected["intent"])
    # Only events following THIS user event belong to this turn.
    events = tracker.get("events", [])
    user_indices = [i for i, event in enumerate(events) if event.get("event") == "user"
                    and event.get("metadata", {}).get("turn_id") == context["turn_id"]]
    check("user_event_found", bool(user_indices), True)
    actual_actions = [e.get("name") for e in events[user_indices[-1]+1:]
                      if e.get("event") == "action"] if user_indices else []
    required = expected.get("actions", [])
    cursor = iter(actual_actions)
    ordered = all(any(actual == action for actual in cursor) for action in required)
    checks.append(dict(name="actions.in_order", actual=actual_actions, expected=required, passed=ordered))
    for entity, value in expected.get("entities", {}).items():
        values = [str(e.get("value")).lower() for e in latest.get("entities", []) if e.get("entity") == entity]
        check("entity."+entity, str(value).lower() in values, True)
    slots = tracker.get("slots", {})
    if "cart_items" in expected:
        raw = slots.get("cart")
        try:
            cart = json.loads(raw) if isinstance(raw, str) and raw else (raw or [])
            def aggregate(items):
                counts = {}
                for item in items:
                    key = (item["product"], item["volume"])
                    counts[key] = counts.get(key, 0) + int(item["quantity"])
                return [dict(product=p, volume=v, quantity=q) for (p, v), q in sorted(counts.items())]
            actual = aggregate([dict(product=i["key"], volume=i["volume"], quantity=i["qty"]) for i in cart])
            check("cart_items", actual, aggregate(expected["cart_items"]))
        except (ValueError, TypeError, KeyError):
            checks.append(dict(name="cart_items", actual=raw, expected=expected["cart_items"], passed=False))
    if "cart" in expected:
        raw = slots.get("cart")
        try:
            cart = json.loads(raw) if isinstance(raw, str) and raw else (raw or [])
            actual = {item["key"]: sum(int(x["qty"]) for x in cart if x["key"] == item["key"]) for item in cart}
        except (ValueError, TypeError, KeyError):
            actual = {"invalid_cart": raw}
        check("cart", actual, expected["cart"])
    for slot, value in expected.get("slots", {}).items():
        check("slot."+slot, slots.get(slot), value)
    text = "\n".join(r.get("text", "") for r in responses).lower()
    check("response.nonempty", bool(text.strip()), True)
    for fragment in expected.get("response_contains", []):
        check("response.contains:"+fragment, fragment.lower() in text, True)
    return checks
