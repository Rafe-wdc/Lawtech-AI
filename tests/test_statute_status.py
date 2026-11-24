"""core.statute_status: which labour statutes are in force.

The failure (2026-11-24, dev and prod): a recovery application "with respect
to the Industrial Relations Code as well as the Industrial Disputes Act" was
drafted under s.33C(2) of the repealed Act, with the Payment of Bonus,
Payment of Gratuity and Factories Acts cited as current law.
"""
import sys

import pytest

sys.path.insert(0, ".")
from core import statute_status as ss  # noqa: E402


class TestRepealedActs:
    def test_twenty_nine_acts_across_four_codes(self):
        # As listed in the repeal sections stored in the legislation index:
        # Wages s.69 (4), IR s.104 (3), SS s.164 (9), OSH s.143 (13).
        counts = {c.name: len(c.repealed) for c in ss.LABOUR_CODES}
        assert counts == {
            "Industrial Relations Code, 2020": 3,
            "Code on Wages, 2019": 4,
            "Code on Social Security, 2020": 9,
            "Occupational Safety, Health and Working Conditions Code, 2020": 13,
        }

    @pytest.mark.parametrize("name, code, section", [
        ("Industrial Disputes Act, 1947", "Industrial Relations Code, 2020", "104"),
        ("industrial dispute act", "Industrial Relations Code, 2020", "104"),
        ("The Industrial Disputes Act 1947", "Industrial Relations Code, 2020", "104"),
        ("ID Act", "Industrial Relations Code, 2020", "104"),
        ("Payment of Gratuity Act, 1972", "Code on Social Security, 2020", "164"),
        ("Payment of Bonus Act", "Code on Wages, 2019", "69"),
        ("Factories Act, 1948", "Occupational Safety, Health and Working Conditions Code, 2020", "143"),
        ("Contract Labour (Regulation and Abolition) Act, 1970",
         "Occupational Safety, Health and Working Conditions Code, 2020", "143"),
    ])
    def test_replacement_found(self, name, code, section):
        r = ss.replacement_for(name)
        assert r is not None and r.code.name == code and r.code.repeal_section == section

    @pytest.mark.parametrize("name", [
        "Industrial Relations Code, 2020",          # the Code itself is in force
        "Bombay Industrial Relations Act, 1946",    # a State Act, not repealed by the Code
        "The United Provinces Industrial Disputes Act, 1947",
        "Industrial Disputes Act, 1950",            # wrong year: not the same statute
        "Negotiable Instruments Act, 1881",
        "",
    ])
    def test_not_a_repealed_labour_act(self, name):
        assert ss.replacement_for(name) is None

    @pytest.mark.parametrize("name", ["IR Code", "Industrial Relations Code", "Code on Wages 2019",
                                      "Code on Social Security Act, 2020"])
    def test_codes_recognised(self, name):
        assert ss.labour_code_named(name) is not None


class TestLawInForceNote:
    def test_empty_when_no_labour_statute(self):
        assert ss.law_in_force_note([]) == ""
        assert ss.law_in_force_note(["Negotiable Instruments Act, 1881", "Hindu Marriage Act, 1955"]) == ""

    def test_names_the_repealed_act_and_its_code(self):
        note = ss.law_in_force_note(["Industrial Relations Code, 2020", "industrial dispute act"])
        assert "21 November 2025" in note
        assert "Industrial Disputes Act, 1947 named in this matter is REPEALED" in note
        assert "Industrial Relations Code, 2020 (section 104)" in note
        assert "section 6 of the General Clauses Act, 1897" in note

    def test_lists_every_code_so_unnamed_acts_are_covered(self):
        # A recovery draft cited the Bonus, Gratuity and Factories Acts although
        # only the ID Act was named.
        note = ss.law_in_force_note(["Industrial Disputes Act, 1947"])
        for act in ("Payment of Bonus Act, 1965", "Payment of Gratuity Act, 1972", "Factories Act, 1948"):
            assert act in note
        assert note.count("REPEALED") == 1

    def test_a_code_alone_still_gets_the_note(self):
        assert "LAW IN FORCE" in ss.law_in_force_note(["Code on Wages, 2019"])


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
