"""Stray CJK glyphs in a non-CJK answer are removed by the output sanitizer.

Repro (2026-11-06, lawyer screenshot): a drafted paragraph read
"In *Sci尊敬 / Chandra Shashi v. Muni Lal Goyal*, (1998) 2 SCC 393, …" —
two Chinese characters emitted by the generation model mid-citation.
"""
import re

from core.sanitize import _strip_stray_cjk, sanitize_output

_CJK = re.compile("[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")

SCREENSHOT = (
    "The conduct of the Respondent constitutes an egregious abuse of the process of the Court "
    "and an attempt to pollute the stream of administration of justice. In *Sci尊敬 / Chandra "
    "Shashi v. Muni Lal Goyal*, (1998) 2 SCC 393, the Supreme Court held that the stream of "
    "justice must be kept pure and unsullied, and anyone who attempts to pollute it by filing "
    "false affidavits or fabricating evidence commits a serious wrong against the judicial "
    "system itself."
)


def test_screenshot_sentence_loses_the_glyphs_and_nothing_else():
    out = _strip_stray_cjk(SCREENSHOT)
    assert not _CJK.search(out)
    assert "In *Sci / Chandra Shashi v. Muni Lal Goyal*, (1998) 2 SCC 393" in out
    assert out.replace("Sci / ", "Sci尊敬 / ") == SCREENSHOT      # only the glyphs went


def test_glyphs_between_spaces_collapse_to_one_space():
    assert _strip_stray_cjk("the Court 尊敬 held that") == "the Court held that"
    assert _strip_stray_cjk("held that 尊敬") == "held that "
    assert _strip_stray_cjk("尊敬 the Court") == " the Court"


def test_japanese_and_korean_glyphs_are_also_stray():
    assert _strip_stray_cjk("Section 138 ありがとう of the Act") == "Section 138 of the Act"
    assert _strip_stray_cjk("Section 138 감사 of the Act") == "Section 138 of the Act"


def test_text_without_cjk_is_returned_unchanged():
    s = "Section 138 of the Negotiable Instruments Act, 1881 — demand notice within 30 days."
    assert _strip_stray_cjk(s) is s


def test_indic_scripts_are_never_touched():
    hindi = "धारा 138 परक्राम्य लिखत अधिनियम, 1881 के अंतर्गत मांग सूचना तीस दिनों के भीतर भेजी जानी चाहिए।"
    tamil = "பிரிவு 138 மாற்றத்தக்க ஆவணங்கள் சட்டம், 1881 இன் கீழ் கோரிக்கை அறிவிப்பு."
    assert _strip_stray_cjk(hindi) == hindi
    assert _strip_stray_cjk(tamil) == tamil


def test_a_genuinely_chinese_answer_is_left_alone():
    zh = "根据1881年《可转让票据法》第138条，付款人必须在收到银行退票通知后三十天内发出书面付款要求。" * 3
    assert _strip_stray_cjk(zh) == zh
    mixed = "Section 138 NI Act: " + zh          # mostly CJK → CJK-language answer
    assert _strip_stray_cjk(mixed) == mixed


def test_sanitize_output_end_to_end_runs_the_pass():
    out = sanitize_output(SCREENSHOT)
    assert not _CJK.search(out)
    assert "Chandra Shashi v. Muni Lal Goyal" in out


def test_a_whole_sentence_that_flipped_to_chinese_inside_an_english_answer_is_removed():
    english = ("The complainant dispatched the statutory demand notice within the prescribed "
               "thirty-day period by registered post to the correct residential address of the "
               "accused, who neither paid nor replied within fifteen days of receipt. ") * 3
    flipped = "被告人在收到通知后十五天内既未付款也未答复。"
    out = _strip_stray_cjk(english + flipped + " The trial court convicted the accused.")
    assert not _CJK.search(out)
    assert out.startswith(english.rstrip(" "))
    assert out.endswith("The trial court convicted the accused.")


# ---------------------------------------------------------------------------
# 2026-11-10: Thai inside a party name, and other scripts no Indian legal
# answer uses. Names below are invented.
# ---------------------------------------------------------------------------

_THAI_IN_NAME = "Dine\u0e02\u0e49\u0e2dhbhai"     # "Dineshbhai" with its "s" glitched


def test_thai_inside_a_name_is_repaired_from_the_correct_spelling_elsewhere():
    text = (f"one {_THAI_IN_NAME} Mehta of Vadgaon introduced the parties. "
            "Later Dineshbhai Mehta confirmed the loan.")
    out = _strip_stray_cjk(text)
    assert out == ("one Dineshbhai Mehta of Vadgaon introduced the parties. "
                   "Later Dineshbhai Mehta confirmed the loan.")


def test_thai_inside_a_name_without_a_reference_spelling_is_just_dropped():
    assert _strip_stray_cjk(f"witness {_THAI_IN_NAME} Mehta") == "witness Dinehbhai Mehta"


def test_two_possible_spellings_are_not_guessed_between():
    text = f"{_THAI_IN_NAME} met Dineshbhai and Dinechbhai."
    assert _strip_stray_cjk(text).startswith("Dinehbhai met")


def test_stray_cyrillic_and_hebrew_are_removed():
    assert _strip_stray_cjk("Section 138 \u0441\u0442\u0430 of the Act") == "Section 138 of the Act"
    assert _strip_stray_cjk("the \u05e9\u05dc\u05d5\u05dd Court") == "the Court"


def test_a_thai_language_answer_is_left_alone():
    thai = "\u0e01\u0e32\u0e23\u0e1e\u0e34\u0e08\u0e32\u0e23\u0e13\u0e32\u0e04\u0e14\u0e35"
    assert _strip_stray_cjk(thai) == thai


def test_greek_and_urdu_are_never_touched():
    greek = "the toxicology report found 2.5 \u03bcg of the alpha (\u03b1) isomer"
    urdu = "\u0639\u062f\u0627\u0644\u062a \u0646\u06d2 \u062d\u06a9\u0645 \u062f\u06cc\u0627"
    assert _strip_stray_cjk(greek) is greek
    assert _strip_stray_cjk(urdu) is urdu
