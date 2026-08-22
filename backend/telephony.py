"""Twilio outbound voice + TwiML webhook handlers + SMS.

Escalation is ALWAYS "Escalate & Notify": bridge the kin to a pre-configured
human responder number and SMS the kin a vitals summary. The responder number
is the ONLY number this system ever bridges to.

Voice is Twilio built-in TTS (<Say>).
TODO(v2): swap say_text() for an ElevenLabs-generated <Play> clip — see the
clearly marked stub at the bottom of this file.
"""

import os
from datetime import datetime
from xml.sax.saxutils import escape

try:
    from twilio.rest import Client as TwilioClient
    from twilio.request_validator import RequestValidator
except ImportError:  # keeps the demo runnable without the twilio package
    TwilioClient = None
    RequestValidator = None


def env(name, default=""):
    return os.environ.get(name, default).strip()


def twilio_ready():
    return bool(TwilioClient and env("TWILIO_ACCOUNT_SID")
                and env("TWILIO_AUTH_TOKEN") and env("TWILIO_FROM_NUMBER"))


def valid_twilio_request(url, params, signature):
    """Authenticate a /voice/* webhook request against X-Twilio-Signature.

    No auth token configured means simulated mode: webhooks are only exercised
    by the offline demo and tests, so validation is not applicable.
    """
    token = env("TWILIO_AUTH_TOKEN")
    if not token:
        return True
    if RequestValidator is None:
        return False
    return RequestValidator(token).validate(url, params, signature or "")


def kin_number(profile):
    return env("KIN_NUMBER") or profile["kin_phone"]


def responder_number(profile):
    return env("RESPONDER_NUMBER") or profile["responder_phone"]


# --------------------------------------------------------------------- scripts
# One distinct opening script per scenario. Filled with live values.
SCRIPTS = {
    "acute": (
        "Hi {kin}, this is the telecare assistant calling on behalf of {name}. "
        "{name}'s heart rate is {hr} beats per minute while they have been "
        "completely still for {duration} seconds. Normally at this time "
        "{name} is {routine}. "
    ),
    "immobility": (
        "Hi {kin}, this is the telecare assistant with a low-urgency check "
        "about {name}. We have detected almost no movement for {duration} "
        "seconds during {name}'s normal waking hours. Normally at this time "
        "{name} is {routine}. "
    ),
    "wandering": (
        "Hi {kin}, this is the telecare assistant calling about {name}. "
        "We are seeing sustained movement during {name}'s usual sleep window, "
        "which is unusual for this time. Their heart rate is {hr} beats per "
        "minute. "
    ),
    "lost_connection": (
        "Hi {kin}, this is the telecare assistant with a low-urgency notice. "
        "We have lost connection with {name}'s watch and have not received "
        "any data for about {gap_min} minutes. This is often just a battery "
        "or signal issue. "
    ),
}

MENU = (
    "Press 1 to escalate and notify {name}'s responder. "
    "Press 2 to stand down."
)


def build_script(profile, scenario, context, routine):
    values = {
        "kin": profile["kin_name"],
        "name": profile["name"],
        "hr": context.get("hr", "unknown"),
        "duration": context.get("duration_sec", "some"),
        "gap_min": context.get("gap_min", "a few"),
        "routine": routine,
    }
    body = SCRIPTS.get(scenario, SCRIPTS["acute"]).format(**values)
    return body + MENU.format(name=profile["name"])


# ----------------------------------------------------------------------- TwiML
def say_text(text):
    """Twilio built-in TTS. TODO(v2): replace with ElevenLabs <Play> clip."""
    return "<Say voice=\"Polly.Joanna\">%s</Say>" % escape(text)


def answer_twiml(profile, scenario, context, routine, base_url, user_id):
    action = "%s/voice/handle?user_id=%s&amp;scenario=%s" % (
        escape(base_url), user_id, scenario)
    script = build_script(profile, scenario, context, routine)
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<Response>"
        "<Gather numDigits=\"1\" timeout=\"10\" action=\"%s\" method=\"POST\">%s</Gather>"
        "%s"
        "<Hangup/>"
        "</Response>"
    ) % (action, say_text(script),
         say_text("We did not receive a response. Goodbye."))


def escalate_twiml(profile):
    """DTMF 1 = Escalate & Notify: bridge kin to the human responder."""
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<Response>"
        "%s"
        "<Dial>%s</Dial>"
        "</Response>"
    ) % (say_text(
        "Escalating and notifying now. Connecting you to %s's responder. "
        "A text message with the details is on its way to you." % profile["name"]),
        escape(responder_number(profile)))


def stand_down_twiml(profile):
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<Response>%s<Hangup/></Response>"
    ) % say_text(
        "Understood, standing down. We have logged this and will keep "
        "monitoring %s. Goodbye." % profile["name"])


def invalid_twiml():
    return (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>"
        "<Response>%s<Hangup/></Response>"
    ) % say_text("Sorry, that was not a valid option. Goodbye.")


# ------------------------------------------------------------------- REST side
class Telephony:
    """Places outbound calls and sends the Escalate & Notify SMS.

    If Twilio credentials are absent, every action is simulated and logged so
    the demo pipeline still runs end-to-end.
    """

    def __init__(self, log_event):
        self.log_event = log_event  # (user_id, type, message, severity) -> None
        # Context of the most recent call per user, so the webhook can build
        # the script with live values.
        self.call_context = {}

    def place_call(self, user_id, profile, scenario, context):
        self.call_context[user_id] = {"scenario": scenario, "context": context}
        to = kin_number(profile)
        base_url = env("PUBLIC_BASE_URL")

        if not twilio_ready() or not base_url:
            self.log_event(
                user_id, "call_simulated",
                "SIMULATED call to kin %s (%s) — scenario %s. Set Twilio env "
                "vars + PUBLIC_BASE_URL for a real call."
                % (profile["kin_name"], to, scenario), "alert")
            return None

        client = TwilioClient(env("TWILIO_ACCOUNT_SID"), env("TWILIO_AUTH_TOKEN"))
        call = client.calls.create(
            to=to,
            from_=env("TWILIO_FROM_NUMBER"),
            url="%s/voice/answer?user_id=%s&scenario=%s" % (base_url, user_id, scenario),
            method="POST",
        )
        self.log_event(user_id, "call_placed",
                       "Outbound call to kin %s (%s), scenario %s, sid %s"
                       % (profile["kin_name"], to, scenario, call.sid), "alert")
        return call.sid

    def send_escalation_sms(self, user_id, profile, scenario, context):
        body = (
            "Telecare Escalate & Notify — %s (%s yrs)\n"
            "Scenario: %s\n"
            "Heart rate: %s bpm\n"
            "Motion: %s\n"
            "Time: %s\n"
            "You are being connected to the responder now."
        ) % (profile["name"], profile["age"], scenario,
             context.get("hr", "n/a"), context.get("motion", "n/a"),
             context.get("reading_ts", datetime.now().isoformat(timespec="seconds")))

        if not twilio_ready():
            self.log_event(user_id, "sms_simulated",
                           "SIMULATED SMS to kin %s: %r"
                           % (kin_number(profile), body.replace("\n", " | ")), "alert")
            return None

        client = TwilioClient(env("TWILIO_ACCOUNT_SID"), env("TWILIO_AUTH_TOKEN"))
        msg = client.messages.create(
            to=kin_number(profile), from_=env("TWILIO_FROM_NUMBER"), body=body)
        self.log_event(user_id, "sms_sent",
                       "Escalation SMS sent to kin %s, sid %s"
                       % (kin_number(profile), msg.sid), "alert")
        return msg.sid


# ------------------------------------------------------------ ElevenLabs stub
def elevenlabs_tts_url(_text):
    """TODO(v2): ElevenLabs TTS integration.

    Plan: POST the script to the ElevenLabs API, cache the MP3 under
    static/tts/, and return a public URL to embed in a TwiML <Play> verb in
    place of say_text(). Explicitly OUT of scope for v1.
    """
    raise NotImplementedError("ElevenLabs TTS is a v2 feature")
