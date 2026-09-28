"""SQL-biased deterministic routing; literature requires explicit knowledge intent."""
import re
from typing import Literal

KnowledgePath = Literal["sql", "literature", "sql+literature"]


def route_question(question: str) -> KnowledgePath:
    text = question.casefold()
    guidance = bool(re.search(
        r"\b(safe|safety|unsafe|comfortable|comfort|healthy|acceptable|normal|"
        r"recommend(?:ed|ations?)?|thresholds?|standards?|guidelines?|wmo|"
        r"too hot|too cold|too humid|too dry|should)\b", text
    ))
    publication = bool(re.search(r"\b(literature|research|papers?|stud(?:y|ies)|articles?)\b", text))
    period = bool(re.search(
        r"\b(today|yesterday|tonight|currently|now|latest|recent|last|past|"
        r"hours?|days?|weeks?|months?|years?|periods?|"
        r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b"
        r"|\b(?:19|20)\d{2}\b|\b\d{4}-\d{2}-\d{2}\b", text
    ))
    observed = bool(re.search(
        r"\b(observed|readings?|measurements?|measured|recorded|our|my|"
        r"trends?|history|what happened)\b", text
    ))
    sensor_topic = bool(re.search(
        r"\b(temperatures?|humidity|pressure|weather|conditions?|sensors?|devices?|room)\b", text
    ))
    statistic = bool(re.search(
        r"\b(averages?|avg|means?|minimum|maximum|min|max|counts?|highest|lowest|"
        r"medians?|how many|how often)\b", text
    ))
    comparison = bool(re.search(r"\b(compare|compared|comparing|comparison|against|versus|vs)\b", text))
    definition = bool(re.search(
        r"\b(define|definitions?|meaning|what is a|what is an|how does|how do)\b", text
    )) or bool(re.fullmatch(r"\s*what is (?:relative humidity|humidity|temperature|pressure)\s*\??\s*", text))
    # Explain measured changes is SQL intent; explain a concept is knowledge intent.
    explanation = bool(re.search(r"\bexplain\b", text)) and not (observed or period or statistic)
    reference = guidance or publication or definition or explanation
    measured = observed or (sensor_topic and comparison) or (sensor_topic and period and not publication)
    if reference and measured:
        return "sql+literature"
    if reference:
        return "literature"
    return "sql"
