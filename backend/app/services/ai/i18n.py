"""Fixed bot replies in English, Tamil and Hindi (FR-M10-04).

Numbers, product names and invoice numbers are filled in by code, never translated by the AI.
Romanised Tamil/Hindi (Tanglish, Hinglish) gets English replies, which is what those buyers read most easily.
"""

TEXTS: dict[str, dict[str, str]] = {
    "greeting": {
        "en": "Hi{name}! Send your order here, e.g. \"10 bags atta, 5 boxes soap\". You can also send a photo of your "
              "list or a voice note. Ask \"where is my order?\" or \"my outstanding\" any time.",
        "ta": "வணக்கம்{name}! உங்கள் ஆர்டரை இங்கே அனுப்புங்கள், எ.கா. \"10 மூட்டை ஆட்டா, 5 பெட்டி சோப்\". "
              "பட்டியலின் புகைப்படம் அல்லது குரல் செய்தியும் அனுப்பலாம். \"என் ஆர்டர் எங்கே?\" அல்லது "
              "\"என் நிலுவை\" என்று எப்போது வேண்டுமானாலும் கேளுங்கள்.",
        "hi": "नमस्ते{name}! अपना ऑर्डर यहाँ भेजें, जैसे \"10 बैग आटा, 5 बॉक्स साबुन\"। आप अपनी लिस्ट की फ़ोटो या "
              "वॉइस नोट भी भेज सकते हैं। \"मेरा ऑर्डर कहाँ है?\" या \"मेरा बकाया\" कभी भी पूछें।",
    },
    "ack": {
        "en": "Thanks! We've received your message. Our team will get back to you shortly.",
        "ta": "நன்றி! உங்கள் செய்தி கிடைத்தது. எங்கள் குழு விரைவில் பதிலளிக்கும்.",
        "hi": "धन्यवाद! आपका संदेश मिल गया है। हमारी टीम जल्द ही आपसे संपर्क करेगी।",
    },
    "handover": {
        "en": "Sure, I've passed this to our team. Someone will reply to you here shortly.",
        "ta": "சரி, இதை எங்கள் குழுவிடம் தெரிவித்துவிட்டேன். விரைவில் ஒருவர் இங்கே பதிலளிப்பார்.",
        "hi": "ठीक है, मैंने यह हमारी टीम को भेज दिया है। जल्द ही कोई यहाँ आपको जवाब देगा।",
    },
    "complaint": {
        "en": "Sorry about this. I've passed it to our team and someone will contact you shortly. "
              "If you can, please send a photo of the problem.",
        "ta": "இதற்கு வருந்துகிறோம். எங்கள் குழுவிடம் தெரிவித்துவிட்டேன், விரைவில் உங்களைத் தொடர்புகொள்வார்கள். "
              "முடிந்தால் பிரச்சினையின் புகைப்படத்தை அனுப்புங்கள்.",
        "hi": "इसके लिए खेद है। मैंने यह हमारी टीम को भेज दिया है, जल्द ही कोई आपसे संपर्क करेगा। "
              "हो सके तो समस्या की फ़ोटो भेजें।",
    },
    "not_yours": {
        "en": "Sorry, I can only share details of your own account.",
        "ta": "மன்னிக்கவும், உங்கள் சொந்த கணக்கு விவரங்களை மட்டுமே பகிர முடியும்.",
        "hi": "क्षमा करें, मैं केवल आपके अपने खाते की जानकारी दे सकता हूँ।",
    },
    "outstanding_none": {
        "en": "You have no outstanding amount with us right now. Thank you!",
        "ta": "தற்போது உங்களுக்கு நிலுவைத் தொகை எதுவும் இல்லை. நன்றி!",
        "hi": "अभी आपका कोई बकाया नहीं है। धन्यवाद!",
    },
    "outstanding_head": {
        "en": "Your outstanding amount is ₹{total}.",
        "ta": "உங்கள் நிலுவைத் தொகை ₹{total}.",
        "hi": "आपका बकाया ₹{total} है।",
    },
    "advance": {
        "en": "You have an advance of ₹{total} with us. Nothing is due.",
        "ta": "உங்களிடமிருந்து ₹{total} முன்பணம் எங்களிடம் உள்ளது. நிலுவை எதுவும் இல்லை.",
        "hi": "आपका ₹{total} अग्रिम हमारे पास है। कुछ भी बकाया नहीं है।",
    },
    "due_list": {
        "en": "Unpaid invoices:",
        "ta": "செலுத்தப்படாத பில்கள்:",
        "hi": "बकाया बिल:",
    },
    "overdue": {"en": "overdue", "ta": "தாமதம்", "hi": "देय तिथि निकल गई"},
    "due_on": {"en": "due", "ta": "கடைசி தேதி", "hi": "देय"},
    "pay_link": {
        "en": "Pay now: {url}",
        "ta": "இப்போது செலுத்த: {url}",
        "hi": "अभी भुगतान करें: {url}",
    },
    "statement_hint": {
        "en": "Reply \"statement\" for your full ledger.",
        "ta": "முழு கணக்கு அறிக்கைக்கு \"statement\" என்று பதிலளிக்கவும்.",
        "hi": "पूरा लेजर पाने के लिए \"statement\" लिखें।",
    },
}


def t(key: str, lang: str | None, **values) -> str:
    texts = TEXTS[key]
    text = texts.get(lang or "en") or texts["en"]
    return text.format(**values) if values else text


def norm_lang(lang: str | None) -> str:
    lang = (lang or "").lower()
    if lang.startswith("ta") and lang != "tanglish":
        return "ta"
    if lang.startswith("hi") and lang != "hinglish":
        return "hi"
    return "en"
