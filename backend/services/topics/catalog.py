from services.schemas.topics import SpeakingTopic, TopicCheckpoint


CATALOG_VERSION = 1


def _topic(
    language, slug, level, label, scenario, goal, learner_role, partner_role,
    criteria, prompts, expressions, keywords,
):
    return SpeakingTopic(
        topic_id=f"{language}-{slug}", version=CATALOG_VERSION,
        language=language, level=level, label=label, scenario=scenario,
        goal=goal, learner_role=learner_role, partner_role=partner_role,
        success_criteria=[
            TopicCheckpoint(id=item[0], label=item[1], evidence_terms=item[2])
            for item in criteria
        ],
        guided_prompts=prompts, support_expressions=expressions,
        keywords=keywords,
    )


TOPICS = (
    _topic("en", "friends-reunion", "B1", "Meeting an old friend", "You meet a friend you have not seen for a long time.", "Reconnect, exchange recent news, and suggest a next meeting.", "An old friend", "Your returning friend", [("recent-news", "Share one piece of recent news", ["recently", "lately", "these days"]), ("next-plan", "Suggest a future plan", ["next time", "let's", "how about"])], ["It has been a long time. What has changed for you?", "What have you been doing lately?", "When should we meet again?"], ["It has been ages", "What have you been up to?", "Let's catch up"], ["friend", "recent", "meet", "catch up"]),
    _topic("en", "shopping-return", "B1", "Returning a purchase", "A product you bought does not work and you take it back to the shop.", "Explain the problem and agree on a practical solution.", "A customer", "A shop assistant", [("problem", "Explain the product problem", ["doesn't work", "broken", "problem"]), ("solution", "Request a solution", ["refund", "exchange", "replace"])], ["How can I help you with this item?", "When did you notice the problem?", "Would you prefer a refund or an exchange?"], ["I'd like to return this", "It doesn't work properly", "Could I get a refund?"], ["shop", "return", "refund", "exchange"]),
    _topic("en", "airport-hotel", "B1", "Airport and hotel problem", "Your flight is delayed and the hotel cannot find your reservation.", "Ask for clear information and arrange accommodation.", "A traveler", "An airline or hotel employee", [("reservation", "Describe the reservation", ["reservation", "booking", "confirmation"]), ("arrangement", "Agree on an arrangement", ["room", "hotel", "available"])], ["Can I see your booking confirmation?", "What happened to your flight?", "What arrangement would work for you tonight?"], ["I have a reservation", "Could you check again?", "What are my options?"], ["airport", "flight", "hotel", "booking"]),
    _topic("en", "shared-housing", "B2", "Solving a shared-housing problem", "Housemates discuss noise, cleaning, and shared responsibilities.", "Raise a concern respectfully and agree on a concrete house rule.", "A housemate", "Another housemate", [("concern", "State a specific concern", ["noise", "cleaning", "bothers"]), ("agreement", "Agree on a house rule", ["agree", "rule", "schedule"])], ["What has been difficult in the flat lately?", "How does that affect you?", "What rule could work for everyone?"], ["Could we talk about", "From my point of view", "Can we agree on"], ["flat", "housemate", "noise", "cleaning"]),
    _topic("en", "job-interview", "B2", "Job interview", "You interview for a role that requires teamwork and problem solving.", "Give a grounded example and ask one relevant question about the role.", "A job candidate", "A hiring manager", [("experience", "Describe relevant experience", ["experience", "project", "responsible"]), ("question", "Ask about the role", ["role", "team", "expectations"])], ["Tell me about a project you are proud of.", "How did you handle a difficult situation?", "What would you like to know about this role?"], ["I was responsible for", "One example is", "Could you tell me more about"], ["job", "interview", "experience", "team"]),
    _topic("en", "workplace-meeting", "C1", "Workplace decision meeting", "A team must choose between a fast launch and a more cautious release.", "Present a position, respond to a concern, and propose a decision criterion.", "A project lead", "A skeptical colleague", [("position", "State and support a position", ["because", "evidence", "recommend"]), ("tradeoff", "Address a trade-off", ["trade-off", "risk", "however"]), ("criterion", "Propose a decision criterion", ["criterion", "measure", "decide"])], ["What is your recommendation and why?", "What risk does the other option create?", "Which criterion should decide this?"], ["The evidence suggests", "The main trade-off is", "I would recommend that"], ["meeting", "decision", "risk", "recommend"]),
    _topic("en", "university-discussion", "C1", "University seminar discussion", "A seminar debates whether universities should require interdisciplinary courses.", "Develop an argument, qualify it, and engage with a counterargument.", "A seminar participant", "Another student", [("argument", "Develop an argument", ["argue", "evidence", "reason"]), ("qualification", "Qualify the claim", ["to some extent", "depends", "provided that"]), ("counterargument", "Address another view", ["counterargument", "however", "although"])], ["What is your main argument?", "Under what conditions might that change?", "How would you answer the strongest objection?"], ["I would argue that", "To some extent", "A possible counterargument is"], ["university", "seminar", "argument", "research"]),
    _topic("de", "friends-reunion", "B1", "Ein Wiedersehen mit Freunden", "Du triffst eine Person, die du lange nicht gesehen hast.", "Neuigkeiten austauschen und ein nächstes Treffen vorschlagen.", "Ein alter Freund oder eine alte Freundin", "Dein Gegenüber beim Wiedersehen", [("recent-news", "Erzähle eine Neuigkeit", ["in letzter zeit", "neulich", "inzwischen"]), ("next-plan", "Schlage ein weiteres Treffen vor", ["nächstes mal", "lass uns", "wie wäre es"])], ["Lange nicht gesehen! Was gibt es Neues?", "Was hast du in letzter Zeit gemacht?", "Wann wollen wir uns wieder treffen?"], ["Lange nicht gesehen", "Was gibt es Neues?", "Lass uns bald wieder treffen"], ["freund", "neuigkeit", "treffen", "wiedersehen"]),
    _topic("de", "shopping-return", "B1", "Eine Ware zurückgeben", "Ein gekauftes Produkt funktioniert nicht und du bringst es zurück.", "Das Problem erklären und eine Lösung vereinbaren.", "Ein Kunde oder eine Kundin", "Eine Verkaufskraft", [("problem", "Erkläre das Problem", ["funktioniert nicht", "kaputt", "problem"]), ("solution", "Bitte um eine Lösung", ["umtausch", "erstatten", "zurückgeben"])], ["Wie kann ich Ihnen mit diesem Artikel helfen?", "Wann ist das Problem aufgetreten?", "Möchten Sie einen Umtausch oder eine Erstattung?"], ["Ich möchte das zurückgeben", "Es funktioniert nicht richtig", "Könnte ich eine Erstattung bekommen?"], ["einkaufen", "zurückgeben", "umtausch", "erstatten"]),
    _topic("de", "airport-hotel", "B1", "Am Flughafen und im Hotel", "Dein Flug ist verspätet und das Hotel findet deine Buchung nicht.", "Informationen erfragen und eine Unterkunft organisieren.", "Ein Reisender oder eine Reisende", "Eine Person am Schalter", [("reservation", "Beschreibe die Buchung", ["buchung", "reservierung", "bestätigung"]), ("arrangement", "Vereinbare eine Lösung", ["zimmer", "hotel", "verfügbar"])], ["Haben Sie eine Buchungsbestätigung?", "Was ist mit Ihrem Flug passiert?", "Welche Lösung wäre für heute Nacht möglich?"], ["Ich habe eine Reservierung", "Könnten Sie noch einmal nachsehen?", "Welche Möglichkeiten habe ich?"], ["flughafen", "flug", "hotel", "buchung"]),
    _topic("de", "shared-housing", "B2", "Ein Problem in der WG lösen", "Mitbewohner sprechen über Lärm, Putzen und gemeinsame Aufgaben.", "Ein Anliegen respektvoll ansprechen und eine konkrete Regel vereinbaren.", "Ein WG-Mitglied", "Ein anderes WG-Mitglied", [("concern", "Nenne ein konkretes Anliegen", ["lärm", "putzen", "stört"]), ("agreement", "Vereinbare eine WG-Regel", ["vereinbaren", "regel", "plan"])], ["Was läuft in der WG gerade schwierig?", "Wie wirkt sich das auf dich aus?", "Welche Regel wäre für alle fair?"], ["Könnten wir über ... sprechen?", "Aus meiner Sicht", "Können wir uns darauf einigen"], ["wg", "mitbewohner", "lärm", "putzen"]),
    _topic("de", "job-interview", "B2", "Vorstellungsgespräch", "Du bewirbst dich auf eine Stelle mit Teamarbeit und Problemlösung.", "Ein konkretes Beispiel geben und eine relevante Frage zur Stelle stellen.", "Ein Bewerber oder eine Bewerberin", "Eine Führungskraft", [("experience", "Beschreibe relevante Erfahrung", ["erfahrung", "projekt", "verantwortlich"]), ("question", "Stelle eine Frage zur Stelle", ["stelle", "team", "erwartungen"])], ["Erzählen Sie von einem Projekt, auf das Sie stolz sind.", "Wie haben Sie eine schwierige Situation gelöst?", "Was möchten Sie über die Stelle wissen?"], ["Ich war verantwortlich für", "Ein Beispiel dafür ist", "Könnten Sie mehr darüber erzählen"], ["bewerbung", "erfahrung", "projekt", "team"]),
    _topic("de", "workplace-meeting", "C1", "Entscheidung im Teammeeting", "Ein Team muss zwischen einem schnellen und einem vorsichtigen Produktstart wählen.", "Eine Position begründen, auf Bedenken eingehen und ein Entscheidungskriterium vorschlagen.", "Eine Projektleitung", "Ein skeptisches Teammitglied", [("position", "Begründe eine Position", ["weil", "belege", "empfehle"]), ("tradeoff", "Behandle einen Zielkonflikt", ["abwägung", "risiko", "allerdings"]), ("criterion", "Schlage ein Kriterium vor", ["kriterium", "messen", "entscheiden"])], ["Was empfehlen Sie und warum?", "Welches Risiko hat die andere Option?", "Welches Kriterium sollte entscheiden?"], ["Die Daten sprechen dafür", "Der zentrale Zielkonflikt ist", "Ich würde empfehlen"], ["meeting", "entscheidung", "risiko", "empfehlen"]),
    _topic("de", "university-discussion", "C1", "Diskussion im Universitätsseminar", "Ein Seminar diskutiert, ob interdisziplinäre Kurse verpflichtend sein sollten.", "Ein Argument entwickeln, einschränken und auf ein Gegenargument eingehen.", "Ein Seminarteilnehmer oder eine Seminarteilnehmerin", "Ein anderes Seminarmitglied", [("argument", "Entwickle ein Argument", ["argument", "beleg", "grund"]), ("qualification", "Schränke die Aussage sinnvoll ein", ["bis zu einem gewissen grad", "hängt davon ab", "vorausgesetzt"]), ("counterargument", "Gehe auf eine Gegenposition ein", ["gegenargument", "allerdings", "obwohl"])], ["Was ist dein Hauptargument?", "Unter welchen Bedingungen würdest du es einschränken?", "Wie antwortest du auf den stärksten Einwand?"], ["Ich würde argumentieren, dass", "Bis zu einem gewissen Grad", "Ein mögliches Gegenargument ist"], ["universität", "seminar", "argument", "forschung"]),
)

BY_ID = {topic.topic_id: topic for topic in TOPICS}


def list_topics(language=None, level=None):
    return [
        topic for topic in TOPICS
        if (language is None or topic.language == language)
        and (level is None or topic.level == level)
    ]


def get_topic(topic_id: str) -> SpeakingTopic:
    try:
        return BY_ID[topic_id]
    except KeyError as exc:
        raise KeyError("Topic not found") from exc
