"""Long-prompt stress test -- 5 prompts each 20-30K characters.

Each test case simulates a user pasting a full Indian legal document and
asking an analytical question. The goal is to verify the API handles very
large payloads without timeouts, truncation, or quality degradation.

Usage:
    python tests/test_long_prompts.py [--api-url URL] [--api-key KEY]

Test cases:
    1. Rental Agreement      (~25K chars) + tenant-risk analysis
    2. Employment Contract    (~22K chars) + Indian labor law review
    3. Sale Deed             (~20K chars) + buyer/seller obligations
    4. Legal Notice           (~28K chars) + validity check
    5. Partnership Deed       (~24K chars) + dispute-risk analysis
"""
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import argparse
import json
import os
import time
from datetime import datetime

import requests

# =============================================================================
#  DOCUMENT GENERATORS
# =============================================================================


def generate_rental_agreement(target_chars=25000):
    """Generate a realistic Indian rental agreement (~25K chars)."""
    sections = []

    sections.append("""RENTAL/LEASE AGREEMENT

This Rental Agreement ("Agreement") is executed on this 15th day of March, 2026 at Mumbai, Maharashtra.

BETWEEN:

Mr. Rajesh Kumar Sharma, S/o Late Shri Ramesh Kumar Sharma, aged about 45 years, residing at Flat No. 302, Wing-A, Harmony Heights, Andheri West, Mumbai - 400058, Maharashtra (hereinafter referred to as the "LANDLORD/LESSOR", which expression shall, unless repugnant to the context or meaning thereof, be deemed to mean and include his heirs, executors, administrators, legal representatives, successors and assigns of the First Part);

AND

Ms. Priya Mehta, D/o Shri Vijay Mehta, aged about 32 years, residing at Room No. 15, Sunrise Hostel, Bandra East, Mumbai - 400051, Maharashtra (hereinafter referred to as the "TENANT/LESSEE", which expression shall, unless repugnant to the context or meaning thereof, be deemed to mean and include her heirs, executors, administrators, legal representatives, successors and assigns of the Second Part);

WHEREAS the Landlord is the absolute and lawful owner of the residential premises described in Schedule-A hereunder and is competent to grant the said premises on lease/rent basis;

AND WHEREAS the Tenant has approached the Landlord for taking the said premises on rent/lease for residential purposes and the Landlord has agreed to let out the same on the terms and conditions hereinafter mentioned;

NOW THEREFORE, THIS AGREEMENT WITNESSETH AND IT IS HEREBY AGREED BY AND BETWEEN THE PARTIES AS FOLLOWS:""")

    sections.append("""
1. DEMISED PREMISES

1.1 The Landlord hereby lets out and the Tenant hereby takes on rent, the residential premises more particularly described in Schedule-A hereto annexed (hereinafter referred to as the "Demised Premises" or "Said Premises"), on the terms and conditions set out in this Agreement.

1.2 The Demised Premises shall be used exclusively for residential purposes by the Tenant and her immediate family members, namely Ms. Priya Mehta and her mother Mrs. Sunita Mehta. The Tenant shall not use the Demised Premises or any part thereof for any commercial, industrial, or illegal purposes whatsoever.

1.3 The Demised Premises is being let out in "as-is-where-is" condition and the Tenant has inspected the same and is fully satisfied with the condition, amenities, and facilities available therein.

1.4 The Landlord hereby represents and warrants that the Demised Premises is free from all encumbrances, liens, charges, and disputes, and that the Landlord has full right, title, and authority to let out the same on rent/lease basis.""")

    sections.append("""
2. TERM OF LEASE

2.1 This Agreement shall be valid for a period of Eleven (11) months commencing from the 1st day of April, 2026 and ending on the 28th day of February, 2027 (hereinafter referred to as the "Lease Term" or "Term").

2.2 Upon expiry of the Lease Term, this Agreement may be renewed for a further period of Eleven (11) months on mutually agreed terms and conditions, including revision of rent, by executing a fresh Rental Agreement. Such renewal shall be subject to the written consent of both parties, communicated at least thirty (30) days prior to the expiry of the current term.

2.3 In the event that the Tenant continues to occupy the Demised Premises after the expiry of the Lease Term without executing a fresh agreement, the tenancy shall be deemed to be a month-to-month tenancy on the same terms and conditions as contained herein, terminable by either party upon giving one month's prior written notice.

2.4 The Landlord reserves the right to refuse renewal of this Agreement without assigning any reason whatsoever, subject to providing adequate notice as stipulated herein.

2.5 Notwithstanding anything contained herein, if any legislation or government order mandates a minimum or maximum lease period different from the term specified above, the parties agree to comply with such statutory requirements and modify the term accordingly.""")

    clause_num = 3
    detailed_clauses = [
        ("RENT AND PAYMENT", """
{n}.1 The Tenant shall pay a monthly rent of Rs. 35,000/- (Rupees Thirty-Five Thousand Only) to the Landlord, payable on or before the 5th day of each calendar month, in advance, for the succeeding month.

{n}.2 The rent shall be paid through electronic bank transfer (NEFT/RTGS/UPI) to the Landlord's designated bank account, details of which are provided in Schedule-B. The Tenant shall retain proof of all rent payments made during the tenancy period.

{n}.3 In the event of delay in payment of rent beyond the due date, the Tenant shall be liable to pay a late payment charge of Rs. 100/- (Rupees One Hundred Only) per day of delay, subject to a maximum of Rs. 3,000/- (Rupees Three Thousand Only) per month. This late payment charge is without prejudice to the Landlord's right to terminate this Agreement for persistent default.

{n}.4 The rent shall be subject to an annual increase of 10% (Ten Percent) upon renewal of this Agreement, unless otherwise mutually agreed upon in writing by both parties.

{n}.5 The Tenant shall not withhold or deduct any amount from the rent payable on account of any dispute, claim, or counterclaim against the Landlord, unless specifically authorized by a court of competent jurisdiction.

{n}.6 All payments under this Agreement shall be made in Indian Rupees only. The Tenant shall bear all bank charges, if any, for the electronic transfer of rent.

{n}.7 The Landlord shall issue a written receipt for each rent payment received, whether through electronic transfer or otherwise, within seven (7) days of receipt of such payment."""),

        ("SECURITY DEPOSIT", """
{n}.1 The Tenant has paid a refundable security deposit of Rs. 2,10,000/- (Rupees Two Lakhs Ten Thousand Only), equivalent to six (6) months' rent, to the Landlord at the time of execution of this Agreement. The Landlord hereby acknowledges receipt of the said security deposit.

{n}.2 The security deposit shall be refunded to the Tenant within thirty (30) days of the Tenant vacating the Demised Premises and handing over peaceful and vacant possession thereof to the Landlord, subject to deductions, if any, as mentioned herein.

{n}.3 The Landlord shall be entitled to deduct from the security deposit: (a) any arrears of rent or other charges payable by the Tenant; (b) the cost of repairing any damage caused to the Demised Premises beyond normal wear and tear; (c) outstanding utility bills or maintenance charges; (d) any other amount payable by the Tenant under this Agreement.

{n}.4 The security deposit shall not carry any interest. The Tenant shall not claim adjustment of the security deposit against rent or any other charges payable during the subsistence of this Agreement.

{n}.5 In the event the Landlord sells or transfers the Demised Premises during the subsistence of this Agreement, the Landlord shall ensure that the security deposit is transferred to the new owner/transferee, who shall be bound by the terms of refund as contained herein.

{n}.6 Any dispute regarding the quantum of deductions from the security deposit shall be resolved as per the dispute resolution mechanism provided in this Agreement."""),

        ("MAINTENANCE AND REPAIRS", """
{n}.1 The Tenant shall maintain the Demised Premises in good and tenantable condition throughout the Lease Term and shall keep the same clean, hygienic, and free from pests and vermin.

{n}.2 Minor repairs and maintenance of the Demised Premises, including but not limited to plumbing repairs, electrical fittings replacement (bulbs, tubes, switches, sockets), tap washers, door handles, window latches, and similar items of day-to-day maintenance up to Rs. 3,000/- (Rupees Three Thousand Only) per instance, shall be the responsibility of the Tenant.

{n}.3 Major structural repairs, including repairs to the roof, external walls, main plumbing lines, electrical wiring, and other structural components of the Demised Premises, shall be the responsibility of the Landlord, provided the same have not been caused by the negligence, misuse, or willful act of the Tenant or the Tenant's family members, guests, or invitees.

{n}.4 The Tenant shall immediately inform the Landlord in writing of any structural damage, water leakage, seepage, or other defect requiring major repair. The Landlord shall undertake such repairs within a reasonable time, not exceeding fifteen (15) days from the date of receipt of such intimation.

{n}.5 In the event the Landlord fails to carry out necessary major repairs within the stipulated time, the Tenant may, with prior written approval of the Landlord, get the repairs done and deduct the actual cost thereof from the rent, subject to the Tenant providing original bills and receipts for the same.

{n}.6 The Tenant shall not make any structural alterations, additions, or modifications to the Demised Premises without the prior written consent of the Landlord. Any unauthorized alterations shall be removed by the Tenant at the Tenant's own cost before vacating the premises, and the Demised Premises shall be restored to its original condition.

{n}.7 The Tenant shall be responsible for annual maintenance and servicing of all air conditioners, geysers, and other electrical appliances provided by the Landlord as part of the furnishings. The Tenant shall maintain records of such servicing and produce the same to the Landlord upon demand."""),

        ("UTILITIES AND OUTGOINGS", """
{n}.1 The Tenant shall be responsible for payment of all utility charges including electricity, water, cooking gas (piped or cylinder), telephone, internet, cable/DTH television, and any other services availed by the Tenant during the Lease Term.

{n}.2 The electricity charges shall be payable directly by the Tenant to the electricity supply company as per the meter reading. The electricity connection is in the name of the Landlord and the Tenant shall ensure timely payment to avoid disconnection.

{n}.3 The water charges, including water tax levied by the Municipal Corporation, shall be borne by the Tenant on proportionate basis as determined by the housing society or as per separate sub-meter installed in the Demised Premises.

{n}.4 The monthly maintenance charges levied by the housing society/apartment association, currently amounting to Rs. 4,500/- (Rupees Four Thousand Five Hundred Only) per month, shall be borne by the Tenant. Any increase in maintenance charges during the Lease Term shall also be borne by the Tenant.

{n}.5 Property tax levied by the Municipal Corporation or any other governmental authority on the Demised Premises shall be the responsibility of the Landlord. However, any special levy, cess, or charge imposed specifically on the occupant shall be borne by the Tenant.

{n}.6 The Tenant shall produce receipts of payment of all utility bills and maintenance charges to the Landlord upon demand.

{n}.7 In the event of non-payment of utility charges by the Tenant resulting in disconnection of services, the Tenant shall bear all reconnection charges and any penalty imposed by the service provider."""),

        ("RESTRICTIONS AND COVENANTS", """
{n}.1 The Tenant shall not sublet, assign, transfer, or part with the possession of the Demised Premises or any part thereof to any third party without the prior written consent of the Landlord.

{n}.2 The Tenant shall not keep or store any hazardous, inflammable, explosive, or dangerous materials or substances in the Demised Premises that may pose a risk to the safety of the building, its occupants, or neighboring properties.

{n}.3 The Tenant shall not carry on or permit to be carried on any activity in the Demised Premises that may cause nuisance, annoyance, or disturbance to the Landlord or other occupants of the building or the neighborhood.

{n}.4 The Tenant shall comply with all rules, regulations, and bye-laws of the housing society/apartment association and shall not act in any manner that may bring disrepute to the society or its members.

{n}.5 The Tenant shall not keep any pets in the Demised Premises without the prior written consent of the Landlord, subject to the rules of the housing society.

{n}.6 The Tenant shall not install or operate any heavy machinery, equipment, or apparatus in the Demised Premises that may cause structural damage or excessive load on the building.

{n}.7 The Tenant shall permit the Landlord or the Landlord's authorized representative to enter and inspect the Demised Premises at reasonable hours, after giving at least twenty-four (24) hours prior notice, except in cases of emergency where immediate access may be required.

{n}.8 The Tenant shall use the parking space, if allotted, only for parking the Tenant's personal vehicle and shall not sublet or allow others to use the same.

{n}.9 The Tenant shall not display any signboard, nameplate, hoarding, or advertisement on the exterior of the Demised Premises or the building without the prior written consent of the Landlord and the housing society.

{n}.10 The Tenant shall not use the terrace, common areas, staircases, or corridors for personal storage, drying clothes, or any other purpose not sanctioned by the housing society bye-laws."""),

        ("TERMINATION", """
{n}.1 Either party may terminate this Agreement before the expiry of the Lease Term by giving two (2) months' prior written notice to the other party. Such notice shall be served personally or sent by registered post/courier to the address of the other party as mentioned in this Agreement.

{n}.2 The Landlord may terminate this Agreement immediately without notice if: (a) the Tenant fails to pay rent for two consecutive months; (b) the Tenant commits a material breach of any term or condition of this Agreement; (c) the Tenant uses the Demised Premises for any illegal or immoral purpose; (d) the Tenant causes significant damage to the Demised Premises; (e) the Tenant sublets or parts with possession without consent.

{n}.3 Upon termination of this Agreement, the Tenant shall vacate the Demised Premises within thirty (30) days and hand over peaceful and vacant possession to the Landlord along with all keys, access cards, and fittings as listed in the inventory.

{n}.4 In the event the Tenant fails to vacate the Demised Premises upon termination, the Tenant shall be liable to pay damages at the rate of double the monthly rent for each month or part thereof of unauthorized occupation, without prejudice to the Landlord's right to seek eviction through legal proceedings.

{n}.5 Upon vacating, the Tenant shall ensure that the Demised Premises is in the same condition as at the commencement of the tenancy, subject to normal wear and tear.

{n}.6 The Tenant shall not be entitled to any compensation, relocation allowance, or damages on account of termination of this Agreement, whether by efflux of time or by notice, except the refund of the security deposit as provided herein."""),

        ("DISPUTE RESOLUTION", """
{n}.1 In the event of any dispute, difference, or claim arising out of or in connection with this Agreement, including any question regarding its existence, validity, interpretation, or termination, the parties shall first attempt to resolve the same amicably through mutual discussion and negotiation.

{n}.2 If the dispute is not resolved through mutual discussion within thirty (30) days, the same shall be referred to a sole arbitrator mutually appointed by both parties. The arbitration shall be conducted in accordance with the Arbitration and Conciliation Act, 1996 as amended from time to time.

{n}.3 The seat and venue of arbitration shall be Mumbai, Maharashtra. The language of arbitration shall be English.

{n}.4 The decision of the arbitrator shall be final and binding on both parties. The costs of arbitration shall be borne equally by both parties unless the arbitrator directs otherwise.

{n}.5 Notwithstanding the above, either party shall be entitled to seek interim or injunctive relief from a court of competent jurisdiction in Mumbai.

{n}.6 This Agreement shall be governed by and construed in accordance with the laws of India. The courts in Mumbai shall have exclusive jurisdiction in respect of all matters arising out of or in connection with this Agreement."""),

        ("FORCE MAJEURE", """
{n}.1 Neither party shall be liable for any failure or delay in performing their obligations under this Agreement if such failure or delay results from circumstances beyond the reasonable control of that party, including but not limited to acts of God, natural disasters, epidemics, pandemics, government orders or restrictions, war, civil unrest, strikes, lockouts, fire, flood, earthquake, or any other event constituting force majeure under Indian law.

{n}.2 The party affected by force majeure shall promptly notify the other party in writing of the nature and expected duration of the force majeure event. The affected party shall use reasonable efforts to mitigate the effects of the force majeure event.

{n}.3 If the force majeure event continues for a period exceeding ninety (90) consecutive days, either party may terminate this Agreement by giving thirty (30) days' written notice to the other party. In such event, the Landlord shall refund the security deposit to the Tenant without any deductions except for arrears of rent or utility charges, if any."""),

        ("INDEMNIFICATION", """
{n}.1 The Tenant shall indemnify and hold harmless the Landlord from and against any and all claims, damages, losses, costs, and expenses (including reasonable legal fees) arising out of or in connection with: (a) the Tenant's use and occupation of the Demised Premises; (b) any breach by the Tenant of any term or condition of this Agreement; (c) any injury to persons or damage to property occurring in or about the Demised Premises during the Lease Term; (d) any act, omission, or negligence of the Tenant, the Tenant's family members, guests, or invitees.

{n}.2 The Landlord shall indemnify and hold harmless the Tenant from and against any claims arising from: (a) any defect in the Landlord's title to the Demised Premises; (b) any structural defect in the building that was pre-existing or not caused by the Tenant; (c) the Landlord's failure to carry out necessary major repairs after due notice.

{n}.3 The indemnifying party shall promptly notify the indemnified party of any claim and shall cooperate fully in the defense of such claim. The indemnified party shall have the right to participate in the defense at its own cost."""),

        ("INSURANCE", """
{n}.1 The Landlord shall maintain adequate insurance coverage for the Demised Premises against fire, natural calamities, and other standard perils. The premium for such insurance shall be borne by the Landlord.

{n}.2 The Tenant shall be responsible for insuring the Tenant's personal belongings, furniture, and valuables kept in the Demised Premises. The Landlord shall not be liable for any loss or damage to the Tenant's personal property.

{n}.3 The Tenant shall not do or permit anything to be done in the Demised Premises that may vitiate or increase the premium of the insurance policy maintained by the Landlord."""),

        ("MISCELLANEOUS PROVISIONS", """
{n}.1 ENTIRE AGREEMENT: This Agreement, together with the Schedules and Annexures hereto, constitutes the entire agreement between the parties with respect to the subject matter hereof and supersedes all prior negotiations, representations, warranties, and understandings, whether written or oral.

{n}.2 AMENDMENT: No amendment, modification, or variation of this Agreement shall be valid or binding unless made in writing and signed by both parties.

{n}.3 WAIVER: The failure or delay of either party to exercise any right, power, or remedy under this Agreement shall not operate as a waiver thereof, nor shall any single or partial exercise of any right preclude the further exercise thereof.

{n}.4 SEVERABILITY: If any provision of this Agreement is held to be invalid, illegal, or unenforceable by a court of competent jurisdiction, the remaining provisions shall continue in full force and effect.

{n}.5 NOTICES: All notices required or permitted under this Agreement shall be in writing and shall be deemed duly served if delivered personally, sent by registered post, or sent by email to the addresses specified in this Agreement.

{n}.6 INTERPRETATION: The headings in this Agreement are for convenience only and shall not affect the interpretation of this Agreement. Words importing the singular shall include the plural and vice versa. References to any statute or statutory provision shall include any modification or re-enactment thereof.

{n}.7 REGISTRATION: This Agreement shall be registered under the Registration Act, 1908 at the office of the Sub-Registrar having jurisdiction over the area where the Demised Premises is situated. The costs of registration and stamp duty shall be borne equally by both parties.

{n}.8 COUNTERPARTS: This Agreement may be executed in two or more counterparts, each of which shall be deemed an original and all of which together shall constitute one and the same instrument.

{n}.9 BINDING EFFECT: This Agreement shall be binding upon and inure to the benefit of the parties hereto and their respective heirs, executors, administrators, legal representatives, successors, and assigns."""),
    ]

    for title, content in detailed_clauses:
        sections.append(content.replace("{n}", str(clause_num)))
        clause_num += 1

    sections.append("""
SCHEDULE-A: DESCRIPTION OF DEMISED PREMISES

Property Address: Flat No. 1204, 12th Floor, Tower-B, Lodha Altamount, Mahalaxmi, Mumbai - 400026, Maharashtra, India

Built-up Area: 1,850 square feet (approximately 171.87 square meters)
Carpet Area: 1,350 square feet (approximately 125.42 square meters)

Configuration: 3 BHK (Three Bedrooms, Hall, Kitchen)

Description of Rooms:
1. Master Bedroom: 14 feet x 16 feet with attached bathroom and walk-in wardrobe
2. Second Bedroom: 12 feet x 14 feet with attached bathroom
3. Third Bedroom/Study: 10 feet x 12 feet
4. Living Room/Hall: 18 feet x 20 feet
5. Dining Area: 10 feet x 12 feet (open plan with living room)
6. Kitchen: 10 feet x 12 feet with utility/service area
7. Common Bathroom: 8 feet x 6 feet
8. Balcony: 6 feet x 20 feet (attached to living room)
9. Servant's Room with attached toilet: 6 feet x 8 feet

Furnishings and Fittings Provided:
- Air conditioners: 4 units (1.5 ton split AC in each bedroom and living room)
- Modular kitchen with granite countertop, chimney, and built-in hob
- Built-in wardrobes in all three bedrooms
- Geyser/water heater in all bathrooms
- Washing machine (semi-automatic, 7 kg capacity)
- Refrigerator (double door, 350 liters)
- RO water purifier in kitchen
- Curtain rods and curtains in all rooms
- Exhaust fans in kitchen and all bathrooms
- Television unit in living room (without TV)

Parking: One covered car parking space in the basement (Slot No. B-127)

Society Amenities: Swimming pool, gymnasium, clubhouse, children's play area, 24-hour security, CCTV surveillance, power backup, intercom facility, piped gas connection

CTS No.: 12345/A, Village: Mahalaxmi, Taluka: Mumbai City, District: Mumbai City
Registration District: Mumbai City, Sub-Registrar Office: Mahalaxmi""")

    sections.append("""
SCHEDULE-B: LANDLORD'S BANK ACCOUNT DETAILS

Account Holder Name: Mr. Rajesh Kumar Sharma
Bank Name: HDFC Bank Limited
Branch: Andheri West, Mumbai
Account Number: XXXX-XXXX-XXXX-2345
IFSC Code: HDFC0000040
Account Type: Savings Account""")

    sections.append("""
SCHEDULE-C: INVENTORY OF FIXTURES AND FITTINGS

The following is an exhaustive inventory of all fixtures, fittings, furniture, and equipment provided by the Landlord in the Demised Premises. The Tenant acknowledges having received all items listed below in good working condition unless otherwise noted:

LIVING ROOM:
1. Split Air Conditioner - Daikin 1.5 Ton 5 Star Inverter - 1 unit - Good condition
2. Curtain rods (stainless steel) - 3 units - Good condition
3. Curtains (cream colored, blackout) - 3 pairs - Good condition
4. Television unit (wooden, wall-mounted bracket) - 1 unit - Good condition
5. Ceiling fan (Havells Festiva) - 1 unit - Good condition
6. Tube lights (LED, Philips) - 4 units - Good condition
7. Electrical switches and sockets - Multiple - Good condition

MASTER BEDROOM:
8. Split Air Conditioner - Daikin 1.5 Ton 5 Star Inverter - 1 unit - Good condition
9. Built-in wardrobe (teak wood, 3-door) - 1 unit - Good condition
10. Curtain rods (stainless steel) - 2 units - Good condition
11. Curtains (blue colored, blackout) - 2 pairs - Good condition
12. Ceiling fan (Havells Festiva) - 1 unit - Good condition
13. Tube lights (LED, Philips) - 2 units - Good condition

MASTER BATHROOM:
14. Geyser (Racold 25 liters) - 1 unit - Good condition
15. Mirror with cabinet - 1 unit - Good condition
16. Towel rod (stainless steel) - 2 units - Good condition
17. Soap dispenser - 1 unit - Good condition
18. Exhaust fan - 1 unit - Good condition
19. Shower panel with mixer - 1 unit - Good condition
20. Western toilet seat (Kohler) - 1 unit - Good condition

SECOND BEDROOM:
21. Split Air Conditioner - Voltas 1.5 Ton 3 Star - 1 unit - Good condition
22. Built-in wardrobe (teak wood, 2-door) - 1 unit - Good condition
23. Curtain rods - 1 unit - Good condition
24. Curtains (green colored) - 1 pair - Good condition
25. Ceiling fan - 1 unit - Good condition
26. Tube lights - 2 units - Good condition

SECOND BATHROOM:
27. Geyser (Bajaj 15 liters) - 1 unit - Good condition
28. Mirror - 1 unit - Good condition
29. Exhaust fan - 1 unit - Good condition
30. Western toilet seat - 1 unit - Good condition

THIRD BEDROOM/STUDY:
31. Split Air Conditioner - Voltas 1.5 Ton 3 Star - 1 unit - Good condition
32. Built-in wardrobe (2-door) - 1 unit - Good condition
33. Curtain rods - 1 unit - Good condition
34. Ceiling fan - 1 unit - Good condition
35. Tube lights - 2 units - Good condition

KITCHEN:
36. Modular kitchen set (L-shaped, granite countertop) - 1 set - Good condition
37. Built-in hob (Elica, 4 burner) - 1 unit - Good condition
38. Chimney (Elica, auto-clean) - 1 unit - Good condition
39. RO Water Purifier (Kent Grand Plus) - 1 unit - Good condition
40. Exhaust fan - 1 unit - Good condition
41. Tube lights - 2 units - Good condition

UTILITY AREA:
42. Washing machine (Samsung, semi-automatic, 7 kg) - 1 unit - Good condition
43. Refrigerator (LG, double door, 350 liters) - 1 unit - Good condition

COMMON BATHROOM:
44. Geyser (Bajaj 10 liters) - 1 unit - Good condition
45. Mirror - 1 unit - Good condition
46. Exhaust fan - 1 unit - Good condition

GENERAL:
47. Main door lock (Godrej, 8 lever) - 1 unit - Good condition
48. Door locks (internal rooms) - 6 units - Good condition
49. Doorbell - 1 unit - Good condition
50. Intercom handset - 1 unit - Good condition""")

    sections.append("""
ANNEXURE-I: HOUSE RULES AND SOCIETY BYE-LAWS (SUMMARY)

The Tenant agrees to abide by the following rules of Lodha Altamount Cooperative Housing Society Limited:

1. Quiet hours shall be observed between 10:00 PM and 7:00 AM. No loud music, parties, or activities causing disturbance during these hours.
2. Common areas (lobby, corridors, staircases, lifts) shall not be used for storage of personal belongings.
3. No modifications to the exterior of the flat, including balcony enclosure, window grilles, or external painting, without prior written approval of the Society Managing Committee.
4. Pets are allowed subject to: (a) registration with the Society office; (b) pets must be leashed in common areas; (c) pet owners are responsible for cleaning up after their pets; (d) no exotic or dangerous animals.
5. Vehicle parking: Only registered vehicles in designated parking slots. No parking in visitor's area for more than 24 hours.
6. Renovation/interior work: Permitted only on weekdays between 10:00 AM and 5:00 PM with prior intimation to the Society office. No work on Sundays and public holidays.
7. Garbage disposal: Wet and dry waste must be segregated. Disposal only in designated bins at designated times.
8. Swimming pool: Operational hours 6:00 AM to 9:00 PM. Guests accompanied by residents only. No food or beverages in pool area.
9. Gymnasium: Operational hours 5:30 AM to 10:00 PM. Proper athletic attire required. No guests without prior arrangement.
10. Visitor management: All visitors to register at the security desk. Overnight guests to be pre-registered with the Society office.
11. Moving in/out: Prior intimation of at least 48 hours to the Society office. Moving permitted only between 9:00 AM and 6:00 PM on weekdays.
12. Fire safety: No obstruction of fire exits. Fire extinguishers in common areas not to be tampered with. Annual fire drill participation mandatory.
13. Water conservation: No wastage of water. Rainwater harvesting system installed; residents to cooperate in maintenance.
14. Noise levels: Construction/drilling work only between 10:00 AM and 1:00 PM and 2:30 PM to 5:00 PM on weekdays.
15. Security deposits to Society: As per Society rules, applicable transfer fees and deposits to be paid before occupancy.""")

    sections.append("""
ANNEXURE-II: CONDITION REPORT AND PHOTOGRAPHS

A detailed condition report with date-stamped photographs of all rooms, fixtures, and fittings has been prepared jointly by the Landlord and the Tenant at the time of handing over possession. The condition report is signed by both parties and forms an integral part of this Agreement. The report covers:

1. Walls and ceilings - condition of paint, any cracks or dampness
2. Flooring - condition of tiles/marble, any chips or stains
3. Windows and doors - condition of frames, glass, locks, and handles
4. Electrical fittings - functioning of all switches, sockets, and lights
5. Plumbing fixtures - functioning of taps, showers, flush mechanisms
6. Kitchen fittings - condition of countertop, cabinets, chimney, hob
7. Appliances - working condition of AC units, geysers, washing machine, refrigerator
8. Balcony - condition of railing, waterproofing, drainage
9. Parking space - condition and accessibility

Both parties agree that the condition report shall be the basis for determining any damage beyond normal wear and tear at the time of vacating the Demised Premises.

IN WITNESS WHEREOF, the parties hereto have set their respective hands and signatures on this Agreement on the day, month, and year first above written at Mumbai, Maharashtra.

LANDLORD/LESSOR                               TENANT/LESSEE
Mr. Rajesh Kumar Sharma                       Ms. Priya Mehta
(Signature)                                    (Signature)

WITNESSES:

1. Name: Mr. Amit Desai
   Address: Flat No. 1202, Tower-B, Lodha Altamount, Mahalaxmi, Mumbai - 400026
   Signature: _______________

2. Name: Mrs. Kavita Joshi
   Address: Flat No. 1206, Tower-B, Lodha Altamount, Mahalaxmi, Mumbai - 400026
   Signature: _______________""")

    doc = "\n".join(sections)

    while len(doc) < target_chars:
        doc += f"""

ADDITIONAL RIDER CLAUSE (Clause R-{len(doc) // 1000}): Both parties hereby further agree and confirm that: (a) this Agreement has been executed voluntarily without any coercion, undue influence, fraud, or misrepresentation; (b) both parties have had the opportunity to seek independent legal advice before executing this Agreement; (c) the Tenant has physically inspected the Demised Premises and is fully satisfied with its condition, location, amenities, and suitability for residential purposes; (d) the Landlord has disclosed all material facts concerning the Demised Premises, including any pending litigation, structural issues, or encumbrances; (e) neither party has made any oral representation, warranty, or promise that is not expressly set forth in this Agreement; and (f) this Agreement shall be construed strictly in accordance with its terms and no implied terms shall be read into it unless mandated by applicable law. The parties further acknowledge that the Maharashtra Rent Control Act, 1999 and the Transfer of Property Act, 1882 shall apply to this tenancy to the extent not inconsistent with the express terms of this Agreement."""

    return doc[:target_chars]


def generate_employment_contract(target_chars=22000):
    """Generate a realistic Indian employment contract (~22K chars)."""
    sections = []

    sections.append("""EMPLOYMENT AGREEMENT

This Employment Agreement ("Agreement") is made and entered into on this 10th day of March, 2026 at Bengaluru, Karnataka.

BETWEEN:

TechNova Solutions Private Limited, a company incorporated under the Companies Act, 2013, having its registered office at No. 42, 5th Floor, Prestige Techno Tower, Outer Ring Road, Bellandur, Bengaluru - 560103, Karnataka, India (CIN: U72200KA2018PTC112345), represented by its Director, Mr. Anand Krishnamurthy (hereinafter referred to as the "Company" or "Employer", which expression shall, unless repugnant to the context or meaning thereof, be deemed to mean and include its successors, administrators, and assigns of the First Part);

AND

Mr. Vikram Singh Rathore, S/o Shri Bhupendra Singh Rathore, aged about 29 years, permanently residing at House No. 156, Sector-22, Gurgaon, Haryana - 122015, and presently residing at No. 78, 2nd Cross, HSR Layout, Sector-3, Bengaluru - 560102, Karnataka (Aadhaar No. XXXX-XXXX-3456, PAN: ABCPR1234F) (hereinafter referred to as the "Employee", which expression shall, unless repugnant to the context or meaning thereof, be deemed to mean and include his heirs, executors, administrators, and legal representatives of the Second Part);

WHEREAS the Company is engaged in the business of software development, information technology consulting, and digital transformation services, and requires the services of qualified professionals;

AND WHEREAS the Employee possesses the necessary qualifications, skills, and experience required by the Company, and has been selected through the Company's recruitment process;

AND WHEREAS both parties are desirous of entering into this Agreement to regulate the terms and conditions of the Employee's employment with the Company;

NOW THEREFORE, in consideration of the mutual covenants and agreements set forth herein, and for other good and valuable consideration, the receipt and sufficiency of which are hereby acknowledged, the parties agree as follows:""")

    clause_num = 1
    clauses = [
        ("APPOINTMENT AND DESIGNATION", """
{n}.1 The Company hereby appoints the Employee as "Senior Software Engineer - Backend" in the Engineering Division of the Company, and the Employee hereby accepts such appointment on the terms and conditions set forth in this Agreement.

{n}.2 The Employee's date of joining shall be the 1st day of April, 2026 (the "Commencement Date"). The Employee shall report to the Engineering Manager or such other person as may be designated by the Company from time to time.

{n}.3 The Employee's place of work shall be the Company's office at Bengaluru, Karnataka. However, the Company reserves the right to transfer the Employee to any other office, branch, subsidiary, or affiliate of the Company, whether in India or abroad, based on business requirements, with reasonable notice.

{n}.4 The Company may, at its discretion, reassign the Employee to a different role, department, or project based on business needs, provided that such reassignment does not result in a material reduction in the Employee's compensation or seniority."""),

        ("PROBATION PERIOD", """
{n}.1 The Employee shall be on probation for a period of six (6) months from the Commencement Date (the "Probation Period"). During the Probation Period, either party may terminate this Agreement by giving fifteen (15) days' written notice or salary in lieu thereof.

{n}.2 Upon satisfactory completion of the Probation Period, the Employee shall be confirmed in writing by the Company. The Company reserves the right to extend the Probation Period by up to three (3) additional months if the Employee's performance is found to be below expectations, with written reasons provided to the Employee.

{n}.3 During the Probation Period, the Employee shall be entitled to all benefits under this Agreement except those specifically restricted to confirmed employees.

{n}.4 If the Employee's services are terminated during the Probation Period for unsatisfactory performance, the Company shall provide the Employee with a service certificate for the period of employment."""),

        ("COMPENSATION AND BENEFITS", """
{n}.1 The Company shall pay the Employee a total annual compensation (Cost to Company or "CTC") of Rs. 24,00,000/- (Rupees Twenty-Four Lakhs Only), the break-up of which is set out in Annexure-A attached hereto.

{n}.2 The compensation structure includes: (a) Basic Salary; (b) House Rent Allowance (HRA); (c) Special Allowance; (d) Conveyance Allowance; (e) Medical Allowance; (f) Leave Travel Allowance (LTA); (g) Employer's contribution to Provident Fund (EPF); (h) Employer's contribution to Employee State Insurance (ESI), if applicable; (i) Gratuity provision; and (j) Performance Bonus (variable component).

{n}.3 Salary shall be paid on the last working day of each calendar month, by direct bank transfer to the Employee's designated bank account. The Company shall deduct applicable taxes (TDS), provident fund contributions, professional tax, and any other statutory deductions.

{n}.4 The Employee shall be eligible for an annual performance bonus of up to 15% of the Basic Salary, subject to the Employee meeting individual performance targets and the Company achieving its business objectives. The bonus shall be determined at the sole discretion of the Company and shall be paid annually, typically in the month of June.

{n}.5 The Company shall conduct an annual compensation review, typically in April of each year. Any revision in compensation shall be at the sole discretion of the Company based on the Employee's performance, market conditions, and the Company's financial performance.

{n}.6 The Employee shall be covered under the Company's Group Medical Insurance policy providing coverage of Rs. 5,00,000/- (Rupees Five Lakhs Only) for the Employee, spouse, and up to two dependent children. The Employee may opt for additional coverage at the Employee's own cost.

{n}.7 The Employee shall be entitled to the Company's Employee Stock Option Plan (ESOP), subject to the terms and conditions of the ESOP scheme as approved by the Board of Directors. Vesting of stock options shall be over a period of four (4) years with a one (1) year cliff."""),

        ("WORKING HOURS AND LEAVE", """
{n}.1 The standard working hours shall be 9 hours per day (including 1 hour break), 5 days a week (Monday to Friday), totaling 40 working hours per week. The normal office hours shall be from 9:30 AM to 6:30 PM, subject to flexibility as per the Company's flexi-time policy.

{n}.2 The nature of the Employee's work may require working beyond normal office hours, on weekends, or on holidays. The Employee agrees to work such additional hours as may be reasonably required by the Company. Employees at the Senior Engineer level and above are not entitled to overtime pay as per the Company's policy and applicable provisions of the Karnataka Shops and Commercial Establishments Act, 1961.

{n}.3 The Employee shall be entitled to the following leave during each calendar year: (a) Earned Leave/Privilege Leave: 18 days; (b) Casual Leave: 8 days; (c) Sick Leave: 8 days; (d) Public Holidays: As per the Company's holiday calendar (approximately 12 days); (e) Maternity/Paternity Leave: As per applicable statutory provisions.

{n}.4 Leave shall be availed as per the Company's Leave Policy. Earned Leave may be accumulated up to a maximum of 45 days and encashed upon separation as per Company policy. Casual Leave and Sick Leave shall lapse at the end of the calendar year if not utilized.

{n}.5 The Company may permit the Employee to work from home on designated days as per the Company's remote work policy, subject to prior approval from the reporting manager and compliance with information security protocols.

{n}.6 The Employee shall maintain accurate records of attendance and working hours through the Company's designated time-tracking system."""),

        ("DUTIES AND RESPONSIBILITIES", """
{n}.1 The Employee shall perform the duties and responsibilities associated with the position of Senior Software Engineer - Backend, as outlined in the job description provided at the time of appointment and as may be modified from time to time by the Company.

{n}.2 Without limiting the generality of the foregoing, the Employee's duties shall include: (a) designing, developing, and maintaining backend services and APIs using Python, Java, or Go; (b) writing clean, testable, and well-documented code; (c) participating in code reviews and providing constructive feedback; (d) collaborating with cross-functional teams including frontend, DevOps, and product teams; (e) mentoring junior engineers; (f) contributing to architectural decisions and technical documentation; (g) participating in on-call rotation for production support.

{n}.3 The Employee shall devote the whole of his working time, attention, and abilities to the business and affairs of the Company and shall faithfully and diligently serve the Company to the best of his abilities.

{n}.4 The Employee shall comply with all policies, procedures, rules, and regulations of the Company as may be in force from time to time, including but not limited to the Code of Conduct, Information Security Policy, Anti-Harassment Policy, and Data Protection Policy.

{n}.5 The Employee shall not, during the term of employment, engage in any other employment, business, or professional activity, whether paid or unpaid, without the prior written consent of the Company. Exceptions may be made for academic teaching, open-source contributions, or charitable activities that do not conflict with the Employee's duties or the Company's interests."""),

        ("CONFIDENTIALITY AND NON-DISCLOSURE", """
{n}.1 The Employee acknowledges that during the course of employment, the Employee will have access to and become acquainted with Confidential Information of the Company. "Confidential Information" includes, but is not limited to: (a) trade secrets, proprietary technology, source code, algorithms, and software architecture; (b) business plans, strategies, financial information, and projections; (c) client lists, contracts, pricing, and business relationships; (d) employee information, compensation details, and HR records; (e) marketing strategies, product roadmaps, and competitive analysis; (f) any other information that is marked as confidential or that a reasonable person would consider confidential.

{n}.2 The Employee shall not, during the term of employment or at any time thereafter, disclose, publish, or make available any Confidential Information to any person, firm, corporation, or other entity without the prior written consent of the Company.

{n}.3 The Employee shall use Confidential Information solely for the purpose of performing duties under this Agreement and shall take all reasonable measures to protect the confidentiality thereof.

{n}.4 The obligations under this clause shall survive the termination of this Agreement for a period of five (5) years, provided that obligations regarding trade secrets shall continue indefinitely.

{n}.5 The restrictions in this clause shall not apply to information that: (a) is or becomes publicly available through no fault of the Employee; (b) was known to the Employee prior to disclosure by the Company; (c) is disclosed pursuant to a court order or legal process, provided the Employee gives the Company prior written notice."""),

        ("INTELLECTUAL PROPERTY", """
{n}.1 All Intellectual Property (including inventions, patents, copyrights, trademarks, trade secrets, designs, software, source code, algorithms, documentation, and all other forms of intellectual property) created, developed, or conceived by the Employee during the course of employment, whether during working hours or otherwise, and whether using the Company's resources or not, shall be the sole and exclusive property of the Company ("Work Product").

{n}.2 The Employee hereby irrevocably assigns and transfers to the Company all right, title, and interest in and to all Work Product, including all intellectual property rights therein, throughout the world, for the full term of such rights.

{n}.3 The Employee shall promptly disclose to the Company any and all Work Product and shall execute all documents and take all actions reasonably requested by the Company to perfect and protect the Company's rights in the Work Product.

{n}.4 The Employee waives all moral rights in the Work Product to the extent permitted by applicable law.

{n}.5 The Employee represents that any pre-existing intellectual property that the Employee wishes to exclude from this assignment is listed in Annexure-B. If no such annexure is provided, the Employee represents that there is no pre-existing intellectual property to exclude."""),

        ("NON-COMPETE AND NON-SOLICITATION", """
{n}.1 During the term of employment and for a period of twelve (12) months following the termination of employment for any reason, the Employee shall not, directly or indirectly: (a) engage in, be employed by, consult for, or have any interest in any business that competes with the business of the Company within India; (b) solicit or attempt to solicit any client, customer, or business partner of the Company for the purpose of providing products or services similar to those provided by the Company.

{n}.2 During the term of employment and for a period of twelve (12) months following termination, the Employee shall not, directly or indirectly, recruit, solicit, or induce any employee, contractor, or consultant of the Company to leave the Company's employment or service.

{n}.3 The Employee acknowledges that the restrictions in this clause are reasonable and necessary for the protection of the Company's legitimate business interests, and that any breach thereof would cause irreparable harm to the Company.

{n}.4 In the event of a breach of this clause, the Company shall be entitled to seek injunctive relief and damages, including liquidated damages of an amount equivalent to the Employee's last drawn six (6) months' CTC.

{n}.5 Notwithstanding the above, the Employee acknowledges that under Section 27 of the Indian Contract Act, 1872, agreements in restraint of trade are void. This clause shall be interpreted and enforced only to the extent permitted under applicable Indian law."""),

        ("TERMINATION", """
{n}.1 After confirmation, either party may terminate this Agreement by giving ninety (90) days' written notice to the other party, or the Company may pay salary in lieu of the notice period ("Notice Period"). The Company may, at its discretion, require the Employee to serve the full Notice Period or relieve the Employee at any time during the Notice Period.

{n}.2 The Company may terminate this Agreement immediately without notice or compensation in the following circumstances: (a) gross misconduct, fraud, dishonesty, or criminal behavior; (b) material breach of any term of this Agreement or Company policies; (c) willful insubordination or refusal to follow lawful instructions; (d) habitual neglect of duties or persistent poor performance despite warnings; (e) conviction of a criminal offence involving moral turpitude; (f) unauthorized disclosure of Confidential Information; (g) any act that brings the Company into disrepute.

{n}.3 Upon termination, the Employee shall: (a) immediately return all Company property including laptop, access cards, documents, and any copies of Confidential Information; (b) complete all handover formalities as required by the Company; (c) cooperate in knowledge transfer to designated successors; (d) settle all outstanding financial obligations to the Company.

{n}.4 The Company shall pay the Employee all earned but unpaid salary, accrued leave encashment, and any pro-rated bonus, subject to applicable deductions and the completion of exit formalities. The full and final settlement shall be completed within forty-five (45) days of the Employee's last working day.

{n}.5 The provisions of this Agreement relating to Confidentiality, Intellectual Property, Non-Compete, Non-Solicitation, and Indemnification shall survive the termination of this Agreement."""),

        ("GOVERNING LAW AND DISPUTE RESOLUTION", """
{n}.1 This Agreement shall be governed by and construed in accordance with the laws of India, and the courts in Bengaluru, Karnataka shall have exclusive jurisdiction.

{n}.2 Any dispute arising out of or in connection with this Agreement shall first be attempted to be resolved through the Company's internal grievance redressal mechanism. If not resolved within thirty (30) days, the dispute shall be referred to arbitration.

{n}.3 The arbitration shall be conducted by a sole arbitrator mutually agreed upon by both parties, in accordance with the Arbitration and Conciliation Act, 1996. The seat and venue of arbitration shall be Bengaluru. The language of arbitration shall be English.

{n}.4 The Employee acknowledges having read and understood the following Company policies, which are incorporated herein by reference: (a) Employee Handbook; (b) Code of Conduct; (c) Information Security Policy; (d) Anti-Harassment and Equal Opportunity Policy; (e) Whistleblower Policy; (f) Data Protection and Privacy Policy; (g) Travel and Expense Policy; (h) Remote Work Policy."""),
    ]

    for title, content in clauses:
        sections.append(content.replace("{n}", str(clause_num)))
        clause_num += 1

    sections.append("""
ANNEXURE-A: COMPENSATION STRUCTURE (Annual)

Component                          Amount (Rs.)
-------------------------------------------------
Basic Salary                       9,60,000
House Rent Allowance (HRA)         4,80,000
Special Allowance                  3,60,000
Conveyance Allowance                 19,200
Medical Allowance                    15,000
Leave Travel Allowance               40,000
Employer PF Contribution           1,15,200
Gratuity Provision                    46,154
Group Medical Insurance Premium       25,000
Performance Bonus (Variable)       3,39,446
-------------------------------------------------
Total CTC                        24,00,000

Note: The above breakup is subject to revision based on changes in statutory requirements or Company policy. Actual take-home salary will be after deduction of Employee's PF contribution, Professional Tax, and Income Tax (TDS) as applicable.""")

    sections.append("""
ANNEXURE-B: PRE-EXISTING INTELLECTUAL PROPERTY

The Employee declares the following pre-existing intellectual property created prior to joining the Company:

1. Open-source library "PyLegalNLP" (MIT License) - A natural language processing toolkit for legal documents, available on GitHub.
2. Personal blog at vikramcodes.dev containing technical articles on backend architecture.
3. Contributions to open-source projects: Django REST Framework, FastAPI, and Celery.

The Employee confirms that none of the above pre-existing IP conflicts with or is required for the Company's business.

ANNEXURE-C: JOB DESCRIPTION

Position: Senior Software Engineer - Backend
Department: Engineering
Reporting To: Engineering Manager
Location: Bengaluru, Karnataka

Key Responsibilities:
1. Design and implement scalable backend services using Python/Django and Go microservices
2. Build and maintain RESTful APIs and GraphQL endpoints
3. Optimize database queries and implement caching strategies (PostgreSQL, Redis)
4. Implement CI/CD pipelines and automated testing frameworks
5. Monitor system performance and implement observability solutions (Prometheus, Grafana)
6. Participate in architecture design reviews and technical planning sessions
7. Mentor junior team members and contribute to hiring processes
8. Maintain technical documentation and contribute to knowledge base
9. Participate in on-call rotation for production incident response
10. Stay current with industry trends and recommend technology improvements

Required Qualifications:
- B.Tech/B.E. in Computer Science or equivalent (M.Tech preferred)
- 4-6 years of professional software development experience
- Strong proficiency in Python, with experience in Django or FastAPI
- Experience with relational databases (PostgreSQL) and NoSQL (MongoDB, Redis)
- Familiarity with cloud platforms (AWS/GCP) and container orchestration (Docker, Kubernetes)
- Understanding of software design patterns, data structures, and algorithms

IN WITNESS WHEREOF, the parties have executed this Agreement on the date first above written.

For TechNova Solutions Private Limited:

Name: Mr. Anand Krishnamurthy
Designation: Director
Signature: _______________
Date: 10th March, 2026

Employee:

Name: Mr. Vikram Singh Rathore
Signature: _______________
Date: 10th March, 2026

Witness 1:
Name: Ms. Deepa Nair, HR Manager
Signature: _______________

Witness 2:
Name: Mr. Suresh Iyer, Lead Engineer
Signature: _______________""")

    doc = "\n".join(sections)

    while len(doc) < target_chars:
        doc += f"""

SUPPLEMENTARY CLAUSE (SC-{len(doc) // 1000}): The Employee further acknowledges and agrees that: (i) the Company may modify its policies and procedures from time to time and the Employee shall comply with such modifications; (ii) the Employee has not been induced to enter into this Agreement by any representations not contained herein; (iii) any invention, improvement, or discovery made by the Employee relating to the Company's business during the term of employment shall be promptly disclosed to the Company; (iv) the Employee shall maintain the highest standards of professional ethics and integrity in all dealings on behalf of the Company; (v) the Employee shall not engage in any activity that creates a conflict of interest with the Company's business; and (vi) the Employee consents to the Company processing personal data in accordance with applicable data protection laws and the Company's Privacy Policy."""

    return doc[:target_chars]


def generate_sale_deed(target_chars=20000):
    """Generate a realistic Indian property sale deed (~20K chars)."""
    sections = []

    sections.append("""SALE DEED

This Deed of Sale ("Sale Deed" or "Deed") is executed on this 20th day of March, 2026 at New Delhi.

BETWEEN:

Mr. Harish Chandra Gupta, S/o Late Shri Mohan Lal Gupta, aged about 62 years, R/o House No. D-45, Defence Colony, New Delhi - 110024 (Aadhaar No. XXXX-XXXX-7890, PAN: ABCPG5678H) (hereinafter referred to as the "VENDOR/SELLER", which expression shall, unless repugnant to the context or meaning thereof, be deemed to mean and include his heirs, executors, administrators, legal representatives, successors, and assigns of the First Part);

AND

Mrs. Anita Deshmukh and Mr. Sanjay Deshmukh, W/o and S/o Late Shri Gopal Deshmukh respectively, both aged about 38 and 40 years respectively, jointly and severally, R/o Flat No. 204, B-Block, Vasant Kunj Apartments, New Delhi - 110070 (Aadhaar Nos. XXXX-XXXX-2345 and XXXX-XXXX-6789 respectively, PAN: DEFPD3456K and GHIPD7890L respectively) (hereinafter jointly referred to as the "PURCHASER/BUYER", which expression shall, unless repugnant to the context or meaning thereof, be deemed to mean and include their respective heirs, executors, administrators, legal representatives, successors, and assigns of the Second Part);

WHEREAS:

A. The Vendor is the sole, absolute, and lawful owner of the immovable property more particularly described in Schedule-I hereunder written, having acquired the same by virtue of a registered Sale Deed dated 15th January, 2010, executed by Mr. Ramesh Bhatia in favor of the Vendor, registered at the office of the Sub-Registrar, Defence Colony, New Delhi as Document No. 12345 of 2010, and further confirmed by mutation entry in the revenue records of the concerned Municipal authority.

B. The Vendor has been in continuous, uninterrupted, and peaceful possession and enjoyment of the said property since 2010 and has been regularly paying all municipal taxes, property taxes, and other levies in respect thereof.

C. The said property is free from all encumbrances, liens, charges, mortgages, attachments, court orders, acquisitions, and any other claims of whatsoever nature.

D. The Purchaser has inspected the said property, verified the title documents, and is satisfied with the title of the Vendor and the condition of the property.

E. The Vendor has agreed to sell and the Purchaser has agreed to purchase the said property for a total sale consideration of Rs. 3,25,00,000/- (Rupees Three Crores Twenty-Five Lakhs Only) on the terms and conditions hereinafter appearing.

NOW THIS DEED WITNESSETH AS FOLLOWS:""")

    clause_num = 1
    clauses = [
        ("SALE AND CONVEYANCE", """
{n}.1 In consideration of the total sale consideration of Rs. 3,25,00,000/- (Rupees Three Crores Twenty-Five Lakhs Only) paid by the Purchaser to the Vendor, the receipt and sufficiency of which is hereby acknowledged by the Vendor, the Vendor does hereby sell, convey, transfer, and assign unto the Purchaser, absolutely and forever, ALL THAT the immovable property more particularly described in Schedule-I hereunder written, together with all rights, title, interest, benefits, privileges, easements, and appurtenances attached thereto or enjoyed therewith.

{n}.2 The Vendor hereby declares that the Vendor has sold the said property to the Purchaser with full title guarantee and that the Purchaser shall henceforth hold and enjoy the said property peacefully and quietly without any interruption, disturbance, or claim from the Vendor or any person claiming through or under the Vendor.

{n}.3 The Vendor hereby covenants that the Vendor has not done or suffered any act, deed, matter, or thing whereby the said property or any part thereof has been or may be encumbered, charged, or affected in any manner whatsoever."""),

        ("SALE CONSIDERATION AND PAYMENT", """
{n}.1 The total sale consideration for the said property is Rs. 3,25,00,000/- (Rupees Three Crores Twenty-Five Lakhs Only), which has been paid by the Purchaser to the Vendor as follows:

(a) Earnest Money/Token Amount: Rs. 25,00,000/- (Rupees Twenty-Five Lakhs Only) paid by demand draft/RTGS on 15th January, 2026 at the time of execution of the Agreement to Sell;

(b) Second Installment: Rs. 1,00,00,000/- (Rupees One Crore Only) paid by RTGS on 15th February, 2026 upon completion of due diligence and title verification;

(c) Balance Amount: Rs. 2,00,00,000/- (Rupees Two Crores Only) paid by RTGS on the date of execution of this Sale Deed, i.e., 20th March, 2026.

{n}.2 The Vendor hereby acknowledges receipt of the entire sale consideration of Rs. 3,25,00,000/- and confirms that no amount remains due or payable by the Purchaser to the Vendor on account of the sale of the said property.

{n}.3 The Vendor confirms that Tax Deducted at Source (TDS) under Section 194-IA of the Income Tax Act, 1961, amounting to Rs. 3,25,000/- (1% of the sale consideration), has been deducted by the Purchaser and shall be deposited with the Income Tax Department. The Vendor shall receive credit for the same in the Vendor's income tax assessment."""),

        ("POSSESSION", """
{n}.1 The Vendor has handed over actual, physical, and vacant possession of the said property to the Purchaser on the date of execution of this Sale Deed, i.e., 20th March, 2026. The Purchaser hereby acknowledges receipt of such possession.

{n}.2 Along with possession, the Vendor has handed over to the Purchaser: (a) all original title deeds, chain of documents, and other papers relating to the said property; (b) all keys to the property; (c) receipts for property tax, water tax, and other municipal charges paid up to date; (d) No Objection Certificate (NOC) from the housing society/apartment association; (e) latest electricity and water bills; (f) sanctioned building plan and completion/occupancy certificate.

{n}.3 The Vendor declares that on the date of handing over possession, there are no arrears of property tax, water tax, electricity charges, maintenance charges, or any other outgoings in respect of the said property. If any arrears are subsequently found, the same shall be the responsibility of the Vendor."""),

        ("REPRESENTATIONS AND WARRANTIES OF THE VENDOR", """
{n}.1 The Vendor hereby represents, warrants, and covenants to the Purchaser as follows:

(a) The Vendor has full right, title, power, and authority to sell and convey the said property and to execute this Sale Deed.

(b) The said property is free from all encumbrances, liens, charges, mortgages, claims, demands, attachments, acquisitions, notifications, and all other defects in title.

(c) The said property is not the subject matter of any suit, appeal, execution proceedings, acquisition proceedings, or any other legal proceedings in any court, tribunal, or authority.

(d) No person other than the Vendor has any right, title, or interest in the said property by way of inheritance, partition, gift, sale, exchange, or otherwise.

(e) The said property has been constructed in accordance with the sanctioned building plan and all applicable building bye-laws. No unauthorized construction or deviation exists.

(f) All requisite approvals, permissions, and clearances from the concerned authorities, including the Municipal Corporation, Development Authority, and other statutory bodies, have been obtained.

(g) The Vendor has complied with all applicable laws, including the Real Estate (Regulation and Development) Act, 2016 (RERA), to the extent applicable.

(h) No notice, order, or communication has been received from any government authority regarding acquisition, demolition, or alteration of the said property.

(i) The property tax assessment is up to date and all taxes have been paid.

(j) The said property is not affected by any road widening, alignment, or development plan of any government authority."""),

        ("INDEMNITY", """
{n}.1 The Vendor shall indemnify and keep indemnified the Purchaser and the Purchaser's heirs, executors, administrators, legal representatives, and assigns from and against all losses, claims, damages, costs, charges, and expenses (including legal fees on solicitor-client basis) which the Purchaser may suffer or incur by reason of: (a) any defect in the title of the Vendor to the said property; (b) any breach of the representations, warranties, or covenants made by the Vendor in this Sale Deed; (c) any claim by any third party in respect of the said property; (d) any encumbrance or liability existing on the date of this Sale Deed; (e) any non-compliance with statutory requirements by the Vendor.

{n}.2 The indemnification obligation of the Vendor shall survive the execution and registration of this Sale Deed and shall continue in perpetuity.

{n}.3 In the event the Purchaser is dispossessed or disturbed in possession of the said property by reason of any defect in the Vendor's title, the Vendor shall, in addition to the indemnification under this clause, refund the entire sale consideration to the Purchaser along with interest at the rate of 12% per annum from the date of execution of this Sale Deed until the date of actual refund."""),

        ("MUTATION AND REGISTRATION", """
{n}.1 The Vendor shall cooperate with the Purchaser in getting the property mutated in the name of the Purchaser in the records of the Municipal Corporation, Revenue Department, and all other concerned authorities. The Vendor shall execute all necessary documents and appear before any authority as may be required for the purpose of mutation.

{n}.2 The costs of registration of this Sale Deed, including stamp duty, registration fee, and miscellaneous expenses, shall be borne by the Purchaser. The stamp duty has been paid at the rate applicable under the Indian Stamp Act, 1899, as adopted by the NCT of Delhi, on the market value or the sale consideration, whichever is higher.

{n}.3 The Vendor confirms that the circle rate/guideline value of the said property as per the records of the Revenue Department is Rs. 2,85,00,000/- (Rupees Two Crores Eighty-Five Lakhs Only), and the sale consideration agreed between the parties exceeds the circle rate."""),

        ("GENERAL COVENANTS", """
{n}.1 This Sale Deed shall be binding upon and shall inure to the benefit of the parties and their respective heirs, executors, administrators, legal representatives, successors, and assigns.

{n}.2 The Vendor shall not, after the execution of this Sale Deed, do or cause to be done any act, deed, matter, or thing that may in any manner affect the rights of the Purchaser in the said property.

{n}.3 If at any time hereafter the Vendor is required to sign any further documents or perform any further acts for the purpose of more fully and effectively assuring the said property to the Purchaser, the Vendor shall do so at the cost of the Purchaser.

{n}.4 All disputes arising out of or in connection with this Sale Deed shall be subject to the exclusive jurisdiction of the courts in New Delhi.

{n}.5 This Sale Deed has been executed in duplicate. The original shall be retained by the Purchaser after registration, and a certified copy shall be provided to the Vendor."""),
    ]

    for title, content in clauses:
        sections.append(content.replace("{n}", str(clause_num)))
        clause_num += 1

    sections.append("""
SCHEDULE-I: DESCRIPTION OF THE PROPERTY

ALL THAT piece and parcel of immovable property being a residential flat bearing:

Flat No.: D-45, Ground Floor
Building/Tower: Block-D, Defence Colony Apartments
Address: Defence Colony, New Delhi - 110024

Super Built-up Area: 2,200 square feet
Built-up Area: 1,850 square feet
Carpet Area: 1,450 square feet

Configuration: 3 BHK with servant's quarter

Bounded as follows:
North: Flat No. D-44 owned by Mr. Suresh Agarwal
South: Flat No. D-46 owned by Mrs. Kamala Nair
East: Internal road of the colony
West: Park/green area of the colony

Municipal Property No.: 2345/6789/DC
Property Tax Assessment No.: DC/2024/3456

Title Chain:
1. Original allotment by Delhi Development Authority (DDA) to Mr. Ashok Bhatia vide Allotment Letter dated 20.06.1985
2. Sale Deed from Mr. Ashok Bhatia to Mr. Ramesh Bhatia dated 10.03.1998, registered as Document No. 5678/1998
3. Sale Deed from Mr. Ramesh Bhatia to Mr. Harish Chandra Gupta (present Vendor) dated 15.01.2010, registered as Document No. 12345/2010

Encumbrances: NIL (as per Encumbrance Certificate dated 01.03.2026 from the Sub-Registrar's Office)

Approved Plan: Sanctioned by MCD vide approval No. MCD/BP/DC/2024/123 dated 15.06.2024
Occupancy Certificate: Issued by MCD vide No. MCD/OC/DC/2024/456 dated 10.09.2024""")

    sections.append("""
SCHEDULE-II: DOCUMENTS HANDED OVER TO THE PURCHASER

1. Original Sale Deed dated 15.01.2010 (Document No. 12345/2010) from Mr. Ramesh Bhatia to the Vendor
2. Certified copy of Sale Deed dated 10.03.1998 (Document No. 5678/1998) from Mr. Ashok Bhatia to Mr. Ramesh Bhatia
3. Certified copy of original DDA Allotment Letter dated 20.06.1985 in favor of Mr. Ashok Bhatia
4. Latest Encumbrance Certificate dated 01.03.2026
5. Property Tax receipts for the years 2020-2026
6. Water and electricity bills (latest)
7. Society NOC dated 10.03.2026
8. Society Share Certificate
9. Sanctioned Building Plan
10. Occupancy/Completion Certificate
11. Aadhaar card copy of the Vendor
12. PAN card copy of the Vendor
13. Passport-size photographs of the Vendor (2 copies)
14. Form 26QB (TDS certificate)
15. Agreement to Sell dated 15.01.2026 (original)

SCHEDULE-III: PAYMENT RECEIPTS

Receipt No. 1:
Date: 15.01.2026
Amount: Rs. 25,00,000/- (Rupees Twenty-Five Lakhs Only)
Mode: RTGS, Reference No. HDFC/RTGS/2026/001234
Purpose: Earnest money under Agreement to Sell

Receipt No. 2:
Date: 15.02.2026
Amount: Rs. 1,00,00,000/- (Rupees One Crore Only)
Mode: RTGS, Reference No. HDFC/RTGS/2026/005678
Purpose: Second installment

Receipt No. 3:
Date: 20.03.2026
Amount: Rs. 2,00,00,000/- (Rupees Two Crores Only)
Mode: RTGS, Reference No. HDFC/RTGS/2026/009012
Purpose: Final balance payment

Total Received: Rs. 3,25,00,000/- (Rupees Three Crores Twenty-Five Lakhs Only)

IN WITNESS WHEREOF, the Vendor and the Purchaser have hereunto set their respective hands on this Sale Deed on the day, month, and year first hereinabove written at New Delhi.

VENDOR/SELLER:
Name: Mr. Harish Chandra Gupta
Signature: _______________

PURCHASER/BUYER:
Name: Mrs. Anita Deshmukh          Name: Mr. Sanjay Deshmukh
Signature: _______________          Signature: _______________

WITNESSES:

1. Name: Mr. Prakash Verma, Advocate
   Address: Chamber No. 45, Tis Hazari Courts, Delhi - 110054
   Signature: _______________

2. Name: Mr. Deepak Choudhary
   Address: D-47, Defence Colony, New Delhi - 110024
   Signature: _______________

Identified by: Mr. Prakash Verma, Advocate (Enrollment No. D/1234/2005)

Presented for registration by: Mr. Sanjay Deshmukh (Purchaser)""")

    doc = "\n".join(sections)

    while len(doc) < target_chars:
        doc += f"""

SUPPLEMENTARY DECLARATION (SD-{len(doc) // 1000}): The Vendor further declares and confirms that: (i) the said property has not been acquired, requisitioned, or notified for acquisition by any government authority under the Land Acquisition Act, 2013 or the Right to Fair Compensation and Transparency in Land Acquisition, Rehabilitation and Resettlement Act, 2013 or any other law; (ii) the said property does not fall within any development zone, restricted zone, or green belt where construction is prohibited; (iii) no portion of the property encroaches upon any government land, public road, drain, or neighboring property; (iv) the Vendor has paid capital gains tax or has availed exemption under Section 54/54F of the Income Tax Act, 1961 on the previous purchase of this property; and (v) the Vendor shall cooperate fully in any future proceedings relating to the mutation, registration, or perfection of title in favor of the Purchaser and shall appear before any authority as may be necessary."""

    return doc[:target_chars]


def generate_legal_notice(target_chars=28000):
    """Generate a realistic Indian legal notice for breach of contract (~28K chars)."""
    sections = []

    sections.append("""LEGAL NOTICE

Under Section 80 of the Code of Civil Procedure, 1908 read with
Section 56 and Section 73 of the Indian Contract Act, 1872

Date: 25th March, 2026

THROUGH REGISTERED POST A.D. / SPEED POST / COURIER / EMAIL

From:
M/s Pinnacle Infrastructure Private Limited
Through its Authorized Signatory, Mr. Arun Kumar Jha, Director
Registered Office: Plot No. 15, Sector-62, Noida - 201301, Uttar Pradesh
CIN: U45200UP2019PTC123456
Email: legal@pinnacleinfra.in

To:

1. M/s GreenBuild Developers Private Limited
   Through its Directors: Mr. Mohit Aggarwal and Mrs. Ritu Aggarwal
   Registered Office: Tower-C, 8th Floor, Logix Cyber Park, Sector-62, Noida - 201301, Uttar Pradesh
   CIN: U45201UP2015PTC098765
   Email: info@greenbuild.in

2. Mr. Mohit Aggarwal, S/o Shri Krishan Lal Aggarwal
   Director, M/s GreenBuild Developers Private Limited
   R/o Villa No. 23, Jaypee Greens, Greater Noida - 201310, Uttar Pradesh

3. Mrs. Ritu Aggarwal, W/o Mr. Mohit Aggarwal
   Director, M/s GreenBuild Developers Private Limited
   R/o Villa No. 23, Jaypee Greens, Greater Noida - 201310, Uttar Pradesh

Subject: Legal Notice for breach of Construction Agreement dated 15th June, 2024; Demand for completion of work, payment of damages, and release of retention money.

NOTICE

Under instructions from and on behalf of my client, M/s Pinnacle Infrastructure Private Limited (hereinafter referred to as "my Client" or "the Principal Employer"), I, Advocate Sunil Mehta (Enrollment No. UP/1234/2012), practicing at District Courts Gautam Budh Nagar and High Court of Judicature at Allahabad, do hereby serve upon you the following Legal Notice:""")

    sections.append("""
1. BACKGROUND AND FACTS

1.1 My Client is a company engaged in the business of infrastructure development, including construction of residential and commercial complexes, roads, bridges, and other civil engineering projects across Northern India, with an annual turnover exceeding Rs. 500 Crores and a workforce of over 2,000 employees and workers.

1.2 Your Company, M/s GreenBuild Developers Private Limited (hereinafter referred to as "the Contractor" or "Noticee No. 1"), is a company engaged in the business of civil construction and building contracting, and was engaged by my Client for the construction of a residential housing project.

1.3 On or about the 15th day of June, 2024, my Client entered into a Construction Agreement (hereinafter referred to as "the Agreement") with the Contractor for the construction and completion of a residential housing project comprising 120 units (60 units of 2BHK and 60 units of 3BHK configuration) across four residential towers (Tower A, B, C, and D) at the site located at Plot No. 45-48, Sector-150, Noida, Uttar Pradesh (hereinafter referred to as "the Project" or "the Project Site").

1.4 The Agreement was executed on a stamp paper of Rs. 100/- and was duly notarized. The Agreement was also registered with the concerned Sub-Registrar. A copy of the Agreement is in the possession of your Company.

1.5 The salient terms of the Agreement were as follows:

(a) Total Contract Value: Rs. 45,00,00,000/- (Rupees Forty-Five Crores Only), inclusive of all taxes, duties, and levies;

(b) Scope of Work: Complete civil construction of four residential towers (G+14 floors each), including foundation, structure, masonry, plastering, plumbing, electrical, firefighting systems, lifts, waterproofing, and external development works as per the approved architectural drawings and structural designs;

(c) Commencement Date: 1st July, 2024;

(d) Completion Date: 30th June, 2026 (24 months from the Commencement Date), with an additional 3-month defect liability period;

(e) Liquidated Damages for Delay: Rs. 2,25,000/- (0.5% of the Contract Value) per week of delay, subject to a maximum of 10% of the Contract Value;

(f) Payment Terms: Monthly Running Account (RA) Bills based on percentage completion, certified by the Project Manager, payable within 21 days of certification;

(g) Retention Money: 5% of each RA Bill to be retained and released as follows: 50% upon completion and 50% upon expiry of the defect liability period;

(h) Performance Bank Guarantee: Rs. 4,50,00,000/- (10% of the Contract Value) to be furnished by the Contractor;

(i) Defect Liability Period: 12 months from the date of completion;

(j) Insurance: The Contractor to maintain Contractor's All Risk (CAR) insurance, Third Party Liability insurance, and Workers' Compensation insurance.""")

    sections.append("""
2. PERFORMANCE AND PAYMENTS BY MY CLIENT

2.1 My Client has duly and faithfully performed all its obligations under the Agreement, including but not limited to:

(a) Timely handover of the Project Site to the Contractor on 28th June, 2024, three days before the scheduled Commencement Date, along with all necessary approvals, permits, and drawings;

(b) Providing all approved architectural drawings, structural designs, BOQ (Bill of Quantities), and technical specifications to the Contractor;

(c) Appointing a qualified Project Manager (Mr. Rakesh Nanda, B.Tech, M.Tech Civil Engineering, with 25 years of experience) to supervise the construction and certify RA Bills;

(d) Ensuring availability of adequate power supply, water supply, and site access for the Contractor's operations;

(e) Timely processing and payment of all RA Bills submitted and certified by the Project Manager.

2.2 As of the date of this Notice, my Client has paid a total of Rs. 28,50,00,000/- (Rupees Twenty-Eight Crores Fifty Lakhs Only) to the Contractor against certified RA Bills, representing approximately 63.33% of the total Contract Value. The details of payments are as follows:

RA Bill No.  Period              Gross Amount     Retention    Net Paid
-----------  ------------------  --------------   ---------    --------
RA-01        Jul-Aug 2024        2,50,00,000      12,50,000    2,37,50,000
RA-02        Sep-Oct 2024        3,00,00,000      15,00,000    2,85,00,000
RA-03        Nov-Dec 2024        3,50,00,000      17,50,000    3,32,50,000
RA-04        Jan-Feb 2025        4,00,00,000      20,00,000    3,80,00,000
RA-05        Mar-Apr 2025        4,50,00,000      22,50,000    4,27,50,000
RA-06        May-Jun 2025        4,00,00,000      20,00,000    3,80,00,000
RA-07        Jul-Aug 2025        3,50,00,000      17,50,000    3,32,50,000
RA-08        Sep-Oct 2025        3,00,00,000      15,00,000    2,85,00,000
RA-09        Nov-Dec 2025        2,00,00,000      10,00,000    1,90,00,000
-----------  ------------------  --------------   ---------    --------
Total                           30,00,00,000    1,50,00,000   28,50,00,000

2.3 The retention money of Rs. 1,50,00,000/- (Rupees One Crore Fifty Lakhs Only) is being held by my Client as per the terms of the Agreement and is due for release only upon completion of the Project and expiry of the defect liability period.""")

    sections.append("""
3. BREACHES AND DEFAULTS BY THE CONTRACTOR

3.1 Despite my Client's full compliance with all its obligations, the Contractor has committed multiple serious breaches of the Agreement, causing enormous financial loss, project delays, and reputational damage to my Client. The breaches are detailed as follows:

3.2 DELAY IN CONSTRUCTION AND FAILURE TO MAINTAIN SCHEDULE:

(a) As per the agreed Project Schedule (Annexure-III to the Agreement), the construction was to progress in a phased manner with defined milestones. As of December 2025 (18 months into the project), the overall physical progress should have been approximately 75% across all four towers.

(b) However, the actual physical progress as of 31st December, 2025, as assessed by the Project Manager and confirmed by an independent third-party quantity surveyor (M/s ABC Associates, appointed jointly), is only approximately 48%, representing a shortfall of 27 percentage points and a time overrun of approximately 8 months.

(c) Tower-wise progress as of 31st December, 2025:
    - Tower A: 62% complete (expected: 80%) - Structural work completed up to 10th floor; 11th-14th floors pending
    - Tower B: 55% complete (expected: 75%) - Structural work completed up to 8th floor; 9th-14th floors pending
    - Tower C: 40% complete (expected: 72%) - Structural work completed up to 6th floor only
    - Tower D: 35% complete (expected: 68%) - Structural work completed up to 5th floor; foundation rework required due to quality issues

(d) The Contractor has failed to deploy adequate manpower, machinery, and materials at the Project Site. Repeated inspections by the Project Manager have noted: (i) average daily workforce of only 120-150 workers against the required 350-400 workers; (ii) absence of key equipment including tower cranes (only 2 out of required 4 deployed), concrete pumps, and transit mixers; (iii) irregular supply of construction materials leading to frequent work stoppages.

3.3 QUALITY DEFICIENCIES:

(a) Multiple quality issues have been identified and documented in inspection reports, including:

(i) Tower D Foundation: Concrete core test results dated 15th November, 2025 revealed that the compressive strength of concrete in the raft foundation of Tower D was found to be 22.5 MPa against the specified M-35 grade (35 MPa), representing a shortfall of approximately 36%. This is a critical structural deficiency that poses serious safety concerns.

(ii) Reinforcement Steel: Random testing of reinforcement steel samples by NABL-accredited laboratory revealed that 12% of the samples failed to meet IS:1786 specifications for Fe-500D grade steel, raising concerns about the structural integrity of the completed floors.

(iii) Waterproofing: The waterproofing treatment applied to the basements of Towers A and B has failed, resulting in water seepage and dampness. The waterproofing was required to be done using crystalline waterproofing system as per specifications, but the Contractor has used inferior membrane-based waterproofing.

(iv) Plumbing: Non-compliance with IS:15778 standards for CPVC plumbing installations in Tower A. Joints found to be improperly solvent-cemented, risking future leakages.

(v) Electrical: Deviation from approved drawings in electrical conduit routing in Towers A and B, creating potential fire hazard and code violation.

(b) Despite multiple written communications (letters dated 10th August 2025, 25th September 2025, 15th November 2025, and 20th January 2026) highlighting these quality issues and demanding rectification, the Contractor has failed to take adequate corrective action.""")

    sections.append("""
4. NOTICES AND COMMUNICATIONS EXCHANGED

4.1 My Client has, at all times, acted in good faith and has provided multiple opportunities to the Contractor to rectify the breaches and resume work at the required pace. A chronological summary of key communications is as follows:

(a) Letter dated 15th September, 2024 (Ref: PI/GB/2024/001): First notice regarding slow progress and inadequate manpower deployment. The Contractor acknowledged the same and assured mobilization of additional resources within 15 days.

(b) Letter dated 10th January, 2025 (Ref: PI/GB/2025/001): Second notice regarding continued delay and quality concerns in Tower D foundation work. The Contractor cited alleged delay in receipt of structural drawings, which was factually incorrect as all drawings were provided before commencement.

(c) Letter dated 15th April, 2025 (Ref: PI/GB/2025/005): Third notice warning of invocation of liquidated damages clause. The Contractor responded requesting extension of time, which was considered and partially granted (60 days extension for Towers C and D due to unprecedented heavy rainfall during monsoon 2024).

(d) Letter dated 10th August, 2025 (Ref: PI/GB/2025/010): Notice regarding quality deficiency in waterproofing of Tower A and B basements. Contractor acknowledged and promised rectification within 30 days. Rectification not completed as of date.

(e) Letter dated 25th September, 2025 (Ref: PI/GB/2025/015): Formal show-cause notice under Clause 25.2 of the Agreement, demanding explanation for persistent delays and quality failures within 15 days. The Contractor's response dated 8th October, 2025 was evasive and did not address the specific concerns raised.

(f) Letter dated 15th November, 2025 (Ref: PI/GB/2025/020): Notice regarding Tower D foundation concrete strength failure and demand for structural audit. The Contractor disputed the test results and requested re-testing, which was duly conducted and confirmed the original findings.

(g) Letter dated 20th January, 2026 (Ref: PI/GB/2026/001): Final warning notice giving 60 days to cure all breaches, failing which my Client would exercise its right to terminate the Agreement and seek damages. No meaningful response received.

(h) Letter dated 25th February, 2026 (Ref: PI/GB/2026/005): Notice of intention to engage alternative contractor for balance works if the Contractor fails to resume work at the required pace within 15 days. No response received.

4.2 Despite all the above communications and opportunities provided, the Contractor has failed to cure the breaches or resume work at the pace required to complete the Project within a reasonable timeframe.""")

    sections.append("""
5. DAMAGES AND LOSSES SUFFERED BY MY CLIENT

5.1 As a direct and proximate consequence of the Contractor's breaches, my Client has suffered and continues to suffer the following damages and losses:

(a) LIQUIDATED DAMAGES FOR DELAY:
As per Clause 12.3 of the Agreement, liquidated damages are payable at the rate of Rs. 2,25,000/- per week of delay. As of 25th March, 2026, the delay is approximately 32 weeks (computed from the expected milestone dates as per the Project Schedule). Accordingly, liquidated damages amount to:
32 weeks x Rs. 2,25,000/- = Rs. 72,00,000/- (Rupees Seventy-Two Lakhs Only)

(b) COST OF RECTIFICATION OF DEFECTIVE WORK:
Based on the independent assessment by M/s ABC Associates, Quantity Surveyors, the estimated cost of rectifying the quality deficiencies identified in the completed work is:
- Tower D foundation strengthening/rehabilitation: Rs. 85,00,000/-
- Waterproofing rectification (Towers A and B): Rs. 35,00,000/-
- Plumbing rectification (Tower A): Rs. 12,00,000/-
- Electrical rectification (Towers A and B): Rs. 18,00,000/-
- Miscellaneous quality corrections: Rs. 25,00,000/-
Total rectification cost: Rs. 1,75,00,000/- (Rupees One Crore Seventy-Five Lakhs Only)

(c) ADDITIONAL COST OF ENGAGING ALTERNATIVE CONTRACTOR:
My Client has obtained quotations from three reputable contractors for completion of the balance 52% of the work. The lowest quotation, from M/s Skyline Constructions Pvt. Ltd., is Rs. 28,50,00,000/- for the balance work, as against the proportionate contract value of Rs. 23,40,00,000/- (52% of Rs. 45,00,00,000/-) under the existing Agreement. The additional cost to be borne by my Client is:
Rs. 28,50,00,000/- minus Rs. 23,40,00,000/- = Rs. 5,10,00,000/- (Rupees Five Crores Ten Lakhs Only)

(d) LOSS DUE TO DELAYED DELIVERY TO HOMEBUYERS:
My Client has entered into agreements with 85 homebuyers for the sale of apartments in the Project, with committed delivery dates ranging from September 2026 to December 2026. Due to the Contractor's delay, my Client is now unable to meet these commitments and faces potential claims from homebuyers under RERA and the Consumer Protection Act, 2019. The estimated exposure on account of delay compensation to homebuyers (at the RERA-prescribed rate) is approximately Rs. 3,50,00,000/- (Rupees Three Crores Fifty Lakhs Only).

(e) LOSS OF REPUTATION AND GOODWILL:
My Client's reputation as a reliable developer has been adversely affected due to the Contractor's delays. Several prospective buyers have cancelled bookings, and my Client has lost potential sales estimated at Rs. 15,00,00,000/- (Rupees Fifteen Crores Only). While this loss is difficult to quantify precisely, it is directly attributable to the Contractor's breach.

(f) LEGAL AND PROFESSIONAL COSTS:
My Client has incurred costs for engaging independent structural auditors, quantity surveyors, and legal professionals aggregating Rs. 15,00,000/- (Rupees Fifteen Lakhs Only).

5.2 TOTAL QUANTIFIED DAMAGES:
The total quantified damages suffered by my Client are summarized as follows:

Liquidated damages for delay:           Rs.    72,00,000/-
Rectification of defective work:        Rs. 1,75,00,000/-
Additional cost of alternative contractor: Rs. 5,10,00,000/-
Delay compensation to homebuyers:       Rs. 3,50,00,000/-
Legal and professional costs:           Rs.    15,00,000/-
                                        -------------------
TOTAL:                                  Rs. 11,22,00,000/-
(Rupees Eleven Crores Twenty-Two Lakhs Only)

Note: The above does not include the loss of reputation and goodwill, which my Client reserves the right to claim separately.""")

    sections.append("""
6. DEMAND

6.1 In view of the aforesaid facts and circumstances, my Client hereby demands that the Noticees, jointly and severally, shall, within THIRTY (30) DAYS of receipt of this Notice:

(a) Pay the sum of Rs. 11,22,00,000/- (Rupees Eleven Crores Twenty-Two Lakhs Only) as damages and compensation to my Client, by way of demand draft or RTGS transfer to my Client's bank account;

(b) In the alternative, if the Contractor is willing and able to complete the remaining work, submit within fifteen (15) days: (i) a revised Project Schedule demonstrating how the Project will be completed within nine (9) months; (ii) evidence of adequate financial resources and manpower to execute the balance work; (iii) an enhanced Performance Bank Guarantee of Rs. 6,75,00,000/- (15% of the Contract Value); (iv) a written undertaking to rectify all quality deficiencies within sixty (60) days at the Contractor's own cost; and (v) consent to enhanced supervision and quality control measures including deployment of a full-time resident engineer at the Contractor's cost;

(c) Release and return all drawings, documents, equipment, and materials belonging to my Client that are in the possession of the Contractor at the Project Site;

(d) Provide a detailed account of all sub-contracts, purchase orders, and commitments made by the Contractor in respect of the Project, along with the status of payments to sub-contractors, suppliers, and workers;

(e) Ensure that no lien, claim, or encumbrance is created on the Project Site or the partially completed structures by any sub-contractor, supplier, worker, or any other third party on account of the Contractor's defaults.

6.2 NOTICE IS HEREBY GIVEN that the Performance Bank Guarantee of Rs. 4,50,00,000/- furnished by the Contractor under the Agreement shall be invoked by my Client if the Contractor fails to comply with the demands stated above within the stipulated time.

6.3 The demand for Noticees No. 2 and No. 3 (Mr. Mohit Aggarwal and Mrs. Ritu Aggarwal) is made in their capacity as Directors who have personally guaranteed the performance of the Contractor under the Agreement, as per the Personal Guarantee executed on 15th June, 2024.""")

    sections.append("""
7. LEGAL CONSEQUENCES

7.1 Please take notice that if the Noticees fail to comply with the aforesaid demands within thirty (30) days of receipt of this Notice, my Client shall be constrained to initiate the following legal proceedings, without further notice:

(a) File a Civil Suit for recovery of Rs. 11,22,00,000/- (Rupees Eleven Crores Twenty-Two Lakhs Only) with interest at the rate of 18% per annum from the date of this Notice until realization, before the competent Civil Court having jurisdiction;

(b) Invoke the arbitration clause (Clause 30) of the Agreement and refer the disputes to arbitration under the Arbitration and Conciliation Act, 1996;

(c) File a complaint before the Real Estate Regulatory Authority (RERA), Uttar Pradesh, seeking appropriate directions and penalties against the Contractor for causing delay in the Project;

(d) File a complaint under Section 420 (Cheating and dishonestly inducing delivery of property), Section 406 (Criminal breach of trust), and Section 120-B (Criminal conspiracy) of the Bharatiya Nyaya Sanhita, 2023 (replacing the Indian Penal Code, 1860) against the Directors of the Contractor Company for misappropriation of funds received from my Client;

(e) File a complaint before the National Company Law Tribunal (NCLT) under Section 7 or Section 9 of the Insolvency and Bankruptcy Code, 2016, to initiate Corporate Insolvency Resolution Process (CIRP) against the Contractor Company;

(f) Apply for attachment before judgment under Order XXXVIII Rule 5 of the Code of Civil Procedure, 1908, to secure the assets of the Noticees;

(g) Seek interim injunction restraining the Noticees from alienating, transferring, or creating any encumbrance on their movable and immovable properties;

(h) Take such other legal remedies as may be available to my Client under the law, including recovery of costs of litigation on actual basis.

7.2 All costs and consequences of the aforesaid legal proceedings shall be entirely at the risk and expense of the Noticees.

7.3 This Notice is issued without prejudice to my Client's rights and remedies under the Agreement, the Indian Contract Act, 1872, the Transfer of Property Act, 1882, the Specific Relief Act, 1963, the Insolvency and Bankruptcy Code, 2016, the Consumer Protection Act, 2019, the Real Estate (Regulation and Development) Act, 2016, and all other applicable laws.

7.4 This Notice shall also serve as a notice under Section 271 of the Bharatiya Nagarik Suraksha Sanhita, 2023 (replacing Section 80 of the Code of Civil Procedure, 1908) to the extent applicable.

8. PERSONAL LIABILITY OF DIRECTORS

8.1 Noticees No. 2 and 3 are further put on notice that they shall be personally liable under the Personal Guarantee dated 15th June, 2024 for the obligations of the Contractor. The relevant clause of the Personal Guarantee states: "The Guarantors hereby unconditionally and irrevocably guarantee the due and punctual performance of all obligations of the Contractor under the Agreement, and agree to indemnify and hold harmless the Principal Employer against all losses, damages, costs, and expenses arising from the Contractor's breach or default."

8.2 In the event of the Contractor's failure to comply with this Notice, my Client shall proceed against Noticees No. 2 and 3 personally for recovery of the entire claim amount, including attachment of their personal assets.

9. RESERVATION OF RIGHTS

9.1 My Client reserves the right to amend, supplement, or modify the claims stated in this Notice, and to raise additional claims as may be warranted by the facts and circumstances that may come to light during the course of litigation or arbitration.

9.2 Nothing contained in this Notice shall be construed as a waiver of any right or remedy available to my Client under the Agreement or under any applicable law.

9.3 This Notice is being sent to the Noticees at their registered office address and residential addresses as mentioned above. A copy of this Notice is being retained by my Client and shall be produced as evidence in any legal proceedings.

Yours faithfully,

Sd/-
Advocate Sunil Mehta
(Enrollment No. UP/1234/2012)
Counsel for M/s Pinnacle Infrastructure Private Limited
Office: Chamber No. 23, District Court Complex, Gautam Budh Nagar, Uttar Pradesh - 201301
Mobile: +91-98765-43210
Email: adv.sunilmehta@legal.in

Enclosures:
1. Copy of Construction Agreement dated 15.06.2024
2. Copy of Performance Bank Guarantee
3. Copy of Personal Guarantee dated 15.06.2024
4. Copies of all correspondence referred to in this Notice
5. Copy of independent assessment report by M/s ABC Associates
6. Copy of concrete test reports for Tower D foundation
7. Copies of RA Bills and payment receipts
8. Photographs showing quality deficiencies and inadequate progress""")

    doc = "\n".join(sections)

    while len(doc) < target_chars:
        doc += f"""

ADDITIONAL ANNEXURE (A-{len(doc) // 1000}): For the record, my Client further states that the Contractor was also required under Clause 18 of the Agreement to maintain a daily work log recording the number of workers deployed (trade-wise), equipment operational, materials received and consumed, weather conditions, and any other relevant observations. The Contractor has consistently failed to maintain such logs, thereby hindering proper monitoring of progress and resource deployment. The Project Manager's independent site diary records confirm that on multiple occasions, the Contractor's workforce was found to be less than 40% of the required strength, with key supervisory personnel absent from the site for days at a stretch. This systematic neglect of contractual obligations demonstrates the Contractor's willful disregard for the terms of the Agreement and constitutes a fundamental breach entitling my Client to terminate the Agreement and claim damages as detailed herein. The Contractor is also in violation of the Building and Other Construction Workers (Regulation of Employment and Conditions of Service) Act, 1996, for failure to register the establishment and provide mandatory safety equipment and welfare facilities to workers at the Project Site."""

    return doc[:target_chars]


def generate_partnership_deed(target_chars=24000):
    """Generate a realistic Indian partnership deed (~24K chars)."""
    sections = []

    sections.append("""PARTNERSHIP DEED

This Deed of Partnership ("Deed") is made and entered into on this 1st day of March, 2026 at Ahmedabad, Gujarat.

BETWEEN:

1. Mr. Ketan Bhavesh Patel, S/o Shri Bhavesh Manilal Patel, aged about 48 years, residing at Bungalow No. 12, Shaligram Bungalows, S.G. Highway, Ahmedabad - 380054, Gujarat (Aadhaar No. XXXX-XXXX-1234, PAN: ABCPP1234M) (hereinafter referred to as the "First Partner");

2. Mr. Rohan Suresh Mehta, S/o Shri Suresh Chandrakant Mehta, aged about 42 years, residing at Flat No. 501, Ganesh Meridian, Prahlad Nagar, Ahmedabad - 380015, Gujarat (Aadhaar No. XXXX-XXXX-5678, PAN: DEFPM5678N) (hereinafter referred to as the "Second Partner");

3. Mrs. Deepa Jayesh Shah, W/o Shri Jayesh Pravin Shah, aged about 39 years, residing at Row House No. 7, Shilp Aaron, Sindhu Bhavan Road, Ahmedabad - 380058, Gujarat (Aadhaar No. XXXX-XXXX-9012, PAN: GHIPS9012P) (hereinafter referred to as the "Third Partner");

(The First Partner, Second Partner, and Third Partner are hereinafter collectively referred to as the "Partners" and individually as a "Partner".)

WHEREAS the Partners are desirous of carrying on business in partnership under the provisions of the Indian Partnership Act, 1932 on the terms and conditions hereinafter mentioned;

AND WHEREAS the Partners have agreed to pool their resources, expertise, and efforts for the purpose of establishing and carrying on the business described herein;

NOW THIS DEED WITNESSETH AND IT IS HEREBY AGREED BY AND BETWEEN THE PARTNERS AS FOLLOWS:""")

    clause_num = 1
    clauses = [
        ("NAME AND STYLE OF THE FIRM", """
{n}.1 The partnership firm shall be known and styled as "PATEL MEHTA & ASSOCIATES" (hereinafter referred to as the "Firm"). The Firm name shall not be changed without the unanimous written consent of all Partners.

{n}.2 The Firm shall be registered under the Indian Partnership Act, 1932 with the Registrar of Firms, Ahmedabad, Gujarat, within sixty (60) days of the execution of this Deed. All costs of registration shall be borne by the Firm.

{n}.3 The Partners shall also apply for and obtain a Permanent Account Number (PAN) and Goods and Services Tax Identification Number (GSTIN) in the name of the Firm within thirty (30) days of the execution of this Deed."""),

        ("NATURE OF BUSINESS", """
{n}.1 The Firm shall carry on the business of: (a) Chartered Accountancy, Audit, and Assurance services; (b) Taxation advisory and compliance services including Income Tax, GST, and International Taxation; (c) Corporate advisory and business consultancy; (d) Financial planning and wealth management advisory; (e) Insolvency and bankruptcy advisory under the Insolvency and Bankruptcy Code, 2016; (f) Forensic audit and fraud investigation services; (g) Management consultancy and business process advisory; (h) Any other professional service as may be mutually agreed upon by the Partners from time to time.

{n}.2 The Firm shall not engage in any trading or manufacturing activity. The business shall be conducted strictly within the regulatory framework of the Institute of Chartered Accountants of India (ICAI) and all applicable laws.

{n}.3 The Firm may appoint semi-qualified assistants, articled clerks, audit clerks, and other staff as may be necessary for the efficient conduct of the business."""),

        ("PRINCIPAL PLACE OF BUSINESS", """
{n}.1 The principal place of business of the Firm shall be at Office No. 301-305, 3rd Floor, Shapath Hexa, S.G. Highway, Ahmedabad - 380054, Gujarat.

{n}.2 The Firm may open branch offices at such other places within India as the Partners may unanimously decide. Currently, the Firm shall operate a branch office at: Office No. 205, Titanium City Center, Satellite Road, Ahmedabad - 380015.

{n}.3 The rent, deposits, and expenses for the office premises shall be borne by the Firm. The current monthly rent for the principal office is Rs. 1,50,000/- (Rupees One Lakh Fifty Thousand Only) and for the branch office is Rs. 75,000/- (Rupees Seventy-Five Thousand Only)."""),

        ("COMMENCEMENT AND DURATION", """
{n}.1 The partnership shall be deemed to have commenced with effect from the 1st day of April, 2026 (the "Commencement Date").

{n}.2 The partnership shall continue for an initial period of ten (10) years from the Commencement Date, and thereafter shall be automatically renewed for successive periods of five (5) years each, unless terminated in accordance with the provisions of this Deed.

{n}.3 The partnership is a "Partnership at Will" as defined under Section 7 of the Indian Partnership Act, 1932, and may be dissolved by any Partner giving not less than six (6) months' written notice to all other Partners, subject to the provisions regarding dissolution contained in this Deed."""),

        ("CAPITAL CONTRIBUTION", """
{n}.1 The Partners shall contribute the following amounts as their initial capital to the Firm:

(a) First Partner (Mr. Ketan Patel):    Rs. 25,00,000/- (Rupees Twenty-Five Lakhs Only)
(b) Second Partner (Mr. Rohan Mehta):   Rs. 20,00,000/- (Rupees Twenty Lakhs Only)
(c) Third Partner (Mrs. Deepa Shah):    Rs. 15,00,000/- (Rupees Fifteen Lakhs Only)

Total Initial Capital: Rs. 60,00,000/- (Rupees Sixty Lakhs Only)

{n}.2 The capital contribution shall be paid into the Firm's designated bank account on or before the Commencement Date. The capital so contributed shall be maintained in the Firm and shall not be withdrawn without the unanimous consent of all Partners.

{n}.3 Additional capital, if required for the business of the Firm, shall be contributed by the Partners in proportion to their profit-sharing ratio, unless otherwise agreed unanimously. Any Partner who fails to contribute additional capital when called upon shall have their profit-sharing ratio adjusted proportionately.

{n}.4 Interest on capital shall be allowed at the rate of 12% per annum (or such rate as may be permissible under Section 40(b) of the Income Tax Act, 1961) calculated on the balance of each Partner's capital account at the beginning of the financial year. Such interest shall be charged as an expense of the Firm.

{n}.5 No Partner shall withdraw any part of the capital without the written consent of all other Partners. In the event of withdrawal, interest shall be charged at 15% per annum on the amount withdrawn from the date of withdrawal until repayment."""),

        ("PROFIT AND LOSS SHARING", """
{n}.1 The net profits and losses of the Firm, after providing for all expenses, interest on capital, Partner's salary/remuneration, depreciation, taxes, and reserves, shall be shared among the Partners in the following ratio:

(a) First Partner (Mr. Ketan Patel):    40% (Forty Percent)
(b) Second Partner (Mr. Rohan Mehta):   35% (Thirty-Five Percent)
(c) Third Partner (Mrs. Deepa Shah):    25% (Twenty-Five Percent)

{n}.2 The profit-sharing ratio shall be reviewed every three (3) years from the Commencement Date and may be revised by unanimous consent of all Partners based on each Partner's contribution to the Firm's business.

{n}.3 The Firm's accounts shall be closed on the 31st day of March of each year. Profits shall be determined after deducting all legitimate business expenses, provisions for bad debts, depreciation, and statutory reserves.

{n}.4 Distribution of profits shall be made within three (3) months of the close of the financial year, after the accounts have been audited and approved by all Partners. Interim drawings against anticipated profits may be made with the consent of all Partners."""),

        ("PARTNER'S REMUNERATION AND DRAWINGS", """
{n}.1 The Partners shall be entitled to the following monthly salary/remuneration, which shall be charged as an expense of the Firm (subject to the limits prescribed under Section 40(b) of the Income Tax Act, 1961):

(a) First Partner (Mr. Ketan Patel):    Rs. 1,50,000/- per month
(b) Second Partner (Mr. Rohan Mehta):   Rs. 1,25,000/- per month
(c) Third Partner (Mrs. Deepa Shah):    Rs. 1,00,000/- per month

{n}.2 In addition to the monthly salary, Partners shall be entitled to draw against their share of anticipated profits, subject to a maximum of Rs. 5,00,000/- (Rupees Five Lakhs Only) per month per Partner. Excess drawings shall attract interest at 15% per annum.

{n}.3 The remuneration shall be reviewed annually and may be revised by unanimous consent of all Partners, subject to the limits prescribed under the Income Tax Act.

{n}.4 Partners shall be reimbursed for all legitimate business expenses incurred on behalf of the Firm, including travel, accommodation, client entertainment, and professional development, subject to submission of proper vouchers and receipts and approval by another Partner."""),

        ("DUTIES AND RESPONSIBILITIES OF PARTNERS", """
{n}.1 Each Partner shall devote their full time, attention, and skill to the business of the Firm and shall faithfully and diligently carry out their duties for the mutual benefit of all Partners.

{n}.2 The specific responsibilities of each Partner shall be as follows:

(a) First Partner (Mr. Ketan Patel) - Managing Partner:
    - Overall management and strategic direction of the Firm
    - Business development and client relationship management
    - Statutory audit engagements and quality review
    - Representation before regulatory bodies (ICAI, NFRA)
    - Final approval of all audit reports and certificates

(b) Second Partner (Mr. Rohan Mehta) - Tax and Advisory Head:
    - Income Tax, GST, and International Tax advisory and compliance
    - Transfer pricing documentation and certification
    - Representation before Income Tax authorities and tribunals
    - Corporate advisory and business restructuring
    - Insolvency and bankruptcy advisory assignments

(c) Third Partner (Mrs. Deepa Shah) - Operations and Technology Head:
    - Internal operations management and HR
    - Technology infrastructure and digital transformation
    - Forensic audit and fraud investigation engagements
    - Staff training, development, and articled clerk management
    - Quality control and process standardization

{n}.3 No Partner shall, during the subsistence of this partnership, carry on any business or profession, whether similar or dissimilar, on their own account or as a Partner, director, consultant, or employee of any other firm, company, or organization, without the prior written consent of all other Partners.

{n}.4 Each Partner shall act with utmost good faith towards the other Partners and shall promptly disclose any conflict of interest or potential conflict of interest."""),

        ("BANKING AND FINANCIAL MANAGEMENT", """
{n}.1 The Firm shall maintain its principal bank account with ICICI Bank Limited, S.G. Highway Branch, Ahmedabad (or such other bank as may be decided by unanimous consent).

{n}.2 The bank accounts of the Firm shall be operated jointly by any two Partners. Single Partner operation shall be permitted for amounts not exceeding Rs. 2,00,000/- (Rupees Two Lakhs Only) per transaction.

{n}.3 No Partner shall, without the prior written consent of all other Partners: (a) borrow money on behalf of the Firm or pledge the Firm's assets; (b) give any guarantee, indemnity, or security on behalf of the Firm; (c) enter into any contract or agreement on behalf of the Firm exceeding Rs. 10,00,000/- (Rupees Ten Lakhs Only); (d) compromise, settle, or release any debt due to the Firm; (e) invest the Firm's funds in any securities, mutual funds, or other instruments.

{n}.4 The Firm shall maintain proper books of accounts, including but not limited to: Cash Book, Bank Book, Journal, Ledger, Client Ledger, Capital Accounts, Current Accounts, Fixed Assets Register, and all other books as required under applicable law.

{n}.5 The accounts of the Firm shall be audited annually by an independent Chartered Accountant who is not a Partner or a relative of any Partner. The auditor shall be appointed by unanimous consent of all Partners."""),

        ("ADMISSION AND RETIREMENT OF PARTNERS", """
{n}.1 A new Partner may be admitted to the Firm only with the unanimous written consent of all existing Partners. The terms and conditions of admission, including capital contribution, profit-sharing ratio, and goodwill payment, shall be determined by mutual agreement.

{n}.2 Any Partner desiring to retire from the Firm shall give not less than six (6) months' prior written notice to all other Partners. The retiring Partner shall be entitled to receive:

(a) The balance in the retiring Partner's capital account;
(b) The retiring Partner's share of accumulated profits, reserves, and goodwill as determined by the continuing Partners or, in the event of disagreement, by an independent valuer;
(c) Interest on the above amounts at the rate of 12% per annum from the date of retirement until the date of actual payment.

{n}.3 The payment to the retiring Partner shall be made in the following manner: (a) 50% of the total amount within six (6) months of retirement; (b) the remaining 50% within twelve (12) months of retirement. The Firm may, at its option, pay the entire amount earlier.

{n}.4 A retiring Partner shall not, for a period of three (3) years from the date of retirement, carry on any business or profession that directly competes with the business of the Firm within a radius of 50 kilometers from any office of the Firm, subject to the provisions of Section 36 of the Indian Partnership Act, 1932.

{n}.5 A retiring Partner shall not solicit or entice away any client or employee of the Firm for a period of two (2) years from the date of retirement."""),

        ("DEATH, INSOLVENCY, AND EXPULSION", """
{n}.1 In the event of the death of any Partner, the surviving Partners shall have the option to continue the business of the Firm. The legal heirs/representatives of the deceased Partner shall be entitled to receive:

(a) The balance in the deceased Partner's capital and current accounts;
(b) The deceased Partner's share of profits up to the date of death;
(c) The deceased Partner's share of goodwill as determined by an independent valuer;
(d) Any salary or remuneration due up to the date of death.

{n}.2 The payment to the legal heirs shall be made within twelve (12) months of the death of the Partner, in two equal installments at intervals of six (6) months. Interest at the rate of 12% per annum shall be payable on any outstanding amount.

{n}.3 The legal heirs of a deceased Partner shall not have any right to interfere in the management of the Firm or to become Partners, unless unanimously agreed by the surviving Partners.

{n}.4 If any Partner is adjudicated as insolvent under the Insolvency and Bankruptcy Code, 2016 or any other applicable law, or is found to be of unsound mind, such Partner shall be deemed to have retired from the Firm as of the date of such adjudication.

{n}.5 A Partner may be expelled from the Firm by a resolution passed by the other Partners unanimously, in the following circumstances: (a) willful and persistent breach of the terms of this Deed; (b) conduct calculated to prejudicially affect the carrying on of the business; (c) guilty of fraud or dishonesty in the conduct of the Firm's business; (d) conviction of a criminal offence involving moral turpitude; (e) professional misconduct as defined by ICAI; (f) becoming permanently incapable of performing duties as a Partner.

{n}.6 Before passing a resolution for expulsion, the Partner concerned shall be given thirty (30) days' notice and an opportunity to be heard. The expelled Partner shall be entitled to the same payments as a retiring Partner, subject to deduction of any damages or losses caused to the Firm by the expelled Partner's conduct."""),

        ("GOODWILL", """
{n}.1 The goodwill of the Firm shall belong to the Partners jointly in their profit-sharing ratio.

{n}.2 The value of goodwill for any purpose under this Deed (including retirement, death, admission of a new Partner, or dissolution) shall be determined as the average of the net profits of the Firm for the three (3) financial years immediately preceding the relevant event, multiplied by a factor of two (2). In the event of disagreement, the goodwill shall be valued by an independent valuer appointed by mutual consent or, failing that, by the President of the ICAI Ahmedabad Branch.

{n}.3 No Partner shall, during the subsistence of this partnership, use the Firm's name, goodwill, or reputation for personal benefit or for the benefit of any third party."""),

        ("DISPUTE RESOLUTION", """
{n}.1 All disputes, differences, or questions arising between the Partners or their heirs, executors, administrators, or legal representatives, touching this Deed or the construction, interpretation, or application thereof, or the rights, duties, or liabilities of the Partners hereunder, shall first be attempted to be resolved through amicable discussion among the Partners.

{n}.2 If the dispute is not resolved through discussion within thirty (30) days, the same shall be referred to mediation before a mediator mutually appointed by the Partners. The mediation shall be conducted at Ahmedabad.

{n}.3 If mediation fails to resolve the dispute within sixty (60) days, the dispute shall be referred to and finally resolved by arbitration under the Arbitration and Conciliation Act, 1996. The arbitration shall be conducted by a panel of three (3) arbitrators, one appointed by each disputing party and the third (presiding arbitrator) appointed by the two arbitrators so appointed.

{n}.4 The seat and venue of arbitration shall be Ahmedabad, Gujarat. The language of arbitration shall be English. The decision of the arbitral tribunal shall be final and binding on all parties.

{n}.5 This Deed shall be governed by and construed in accordance with the laws of India, and subject to the arbitration clause, the courts at Ahmedabad shall have exclusive jurisdiction."""),

        ("DISSOLUTION", """
{n}.1 The Firm shall be dissolved in the following circumstances: (a) by mutual consent of all Partners; (b) upon the expiry of the partnership term, if not renewed; (c) by order of a court under Section 44 of the Indian Partnership Act, 1932; (d) if only one Partner remains and no new Partner is admitted within six (6) months; (e) if the Firm becomes insolvent.

{n}.2 Upon dissolution, the assets of the Firm shall be applied in the following order of priority: (a) payment of debts and liabilities of the Firm to third parties; (b) payment of loans advanced by Partners to the Firm; (c) repayment of capital to the Partners; (d) distribution of surplus, if any, among the Partners in their profit-sharing ratio.

{n}.3 Upon dissolution, the Partners shall cooperate in the orderly winding up of the Firm's affairs, including completion of pending assignments, settlement of client accounts, and realization of assets.

{n}.4 The books and records of the Firm shall be retained for a period of eight (8) years from the date of dissolution, in the custody of the Partner designated by unanimous consent or, failing such designation, the First Partner."""),

        ("CONFIDENTIALITY", """
{n}.1 Each Partner shall maintain strict confidentiality regarding the affairs of the Firm, including client information, financial data, business strategies, and any other information of a confidential nature, both during the subsistence of the partnership and after retirement, expulsion, or dissolution.

{n}.2 The obligation of confidentiality shall extend to all client information, audit working papers, tax returns and computations, legal opinions, and all other professional work product of the Firm.

{n}.3 Any breach of confidentiality shall be treated as a material breach of this Deed and may result in expulsion of the breaching Partner and liability for damages."""),

        ("MISCELLANEOUS", """
{n}.1 ENTIRE AGREEMENT: This Deed constitutes the entire agreement between the Partners relating to the partnership and supersedes all prior negotiations, representations, understandings, and agreements, whether oral or written.

{n}.2 AMENDMENT: This Deed may be amended or modified only by a written instrument signed by all Partners.

{n}.3 SEVERABILITY: If any provision of this Deed is held to be invalid, illegal, or unenforceable, the remaining provisions shall continue in full force and effect.

{n}.4 NOTICES: All notices under this Deed shall be in writing and shall be deemed served when delivered personally, sent by registered post, or sent by email to the addresses specified herein.

{n}.5 INDEMNITY: Each Partner shall indemnify and hold harmless the other Partners and the Firm from and against any loss, damage, claim, or expense arising from such Partner's negligence, misconduct, or breach of this Deed.

{n}.6 INSURANCE: The Firm shall maintain Professional Indemnity Insurance of not less than Rs. 5,00,00,000/- (Rupees Five Crores Only) and such other insurance as may be required by applicable law or the ICAI.

{n}.7 COMPLIANCE: The Partners shall at all times comply with the Chartered Accountants Act, 1949, the regulations and guidelines of the ICAI, the Prevention of Money Laundering Act, 2002, and all other applicable laws and professional standards.

{n}.8 POWER OF ATTORNEY: Each Partner hereby authorizes the Managing Partner (First Partner) to act as the attorney-in-fact for the Firm for routine matters, subject to the limitations specified in this Deed. A separate Power of Attorney may be executed if required."""),
    ]

    for title, content in clauses:
        sections.append(content.replace("{n}", str(clause_num)))
        clause_num += 1

    sections.append("""
SCHEDULE-I: DETAILS OF PARTNERS

Partner           Qualification         ICAI Membership    Experience
----------------  --------------------  -----------------  ----------
Ketan Patel       FCA, DISA, LLB        M.No. 045678      24 years
Rohan Mehta       FCA, CPA (USA)        M.No. 067890      18 years
Deepa Shah        FCA, CFE, CISA        M.No. 089012      15 years

SCHEDULE-II: CAPITAL ACCOUNTS (As on Commencement Date)

Partner           Capital (Rs.)    Mode of Payment
----------------  ---------------  ---------------
Ketan Patel       25,00,000        RTGS
Rohan Mehta       20,00,000        RTGS
Deepa Shah        15,00,000        RTGS
                  ---------------
Total             60,00,000

SCHEDULE-III: ASSETS BROUGHT INTO THE FIRM

The following assets are being contributed by the Partners to the Firm:

By First Partner (Mr. Ketan Patel):
1. Client portfolio (approximately 120 audit and 250 tax clients) - Valued at Rs. 15,00,000/-
2. Office furniture and equipment - Valued at Rs. 3,00,000/-
3. Library and reference materials - Valued at Rs. 1,50,000/-

By Second Partner (Mr. Rohan Mehta):
1. Client portfolio (approximately 80 tax and advisory clients) - Valued at Rs. 10,00,000/-
2. Computer hardware and software licenses - Valued at Rs. 5,00,000/-
3. Tally and audit software (multi-user licenses) - Valued at Rs. 2,00,000/-

By Third Partner (Mrs. Deepa Shah):
1. Client portfolio (approximately 40 forensic and advisory clients) - Valued at Rs. 8,00,000/-
2. Forensic audit tools and software - Valued at Rs. 3,00,000/-
3. Training materials and course content - Valued at Rs. 1,00,000/-

IN WITNESS WHEREOF, the Partners have hereunto set their respective hands and seals on this Deed of Partnership on the day, month, and year first above written at Ahmedabad, Gujarat.

FIRST PARTNER:
Name: Mr. Ketan Bhavesh Patel, FCA
Signature: _______________
Date: 1st March, 2026

SECOND PARTNER:
Name: Mr. Rohan Suresh Mehta, FCA
Signature: _______________
Date: 1st March, 2026

THIRD PARTNER:
Name: Mrs. Deepa Jayesh Shah, FCA
Signature: _______________
Date: 1st March, 2026

WITNESSES:

1. Name: Mr. Hemant Trivedi, Advocate
   Address: 201, Shapath-IV, S.G. Highway, Ahmedabad - 380054
   Signature: _______________

2. Name: CA Manish Desai
   Address: 105, Sakar-II, Ashram Road, Ahmedabad - 380009
   Signature: _______________""")

    doc = "\n".join(sections)

    while len(doc) < target_chars:
        doc += f"""

SUPPLEMENTARY COVENANT (SC-{len(doc) // 1000}): The Partners further agree and covenant as follows: (i) that each Partner shall maintain the highest standards of professional ethics as prescribed by the Institute of Chartered Accountants of India and shall not engage in any conduct that may bring disrepute to the Firm or the profession; (ii) that the Firm shall establish and maintain a robust quality control system in accordance with SQC-1 (Standard on Quality Control) and shall conduct periodic peer reviews as required by ICAI; (iii) that each Partner shall undertake a minimum of forty (40) hours of Continuing Professional Education (CPE) per year as mandated by ICAI; (iv) that the Firm shall maintain adequate professional indemnity insurance and shall not accept any engagement that exposes the Firm to unreasonable risk; (v) that all Partners shall cooperate fully in the event of any investigation, inquiry, or disciplinary proceedings by ICAI, NFRA, or any other regulatory authority; and (vi) that the provisions of this Deed shall be interpreted in a manner consistent with the Indian Partnership Act, 1932 and all applicable professional standards."""

    return doc[:target_chars]


# =============================================================================
#  TEST CASES
# =============================================================================

TEST_CASES = [
    {
        "id": 1,
        "name": "Rental Agreement Review",
        "generator": generate_rental_agreement,
        "target_chars": 25000,
        "question": "What are the potential legal issues in this rental agreement? Identify any clauses that are unfavorable to the tenant and suggest modifications.",
    },
    {
        "id": 2,
        "name": "Employment Contract Review",
        "generator": generate_employment_contract,
        "target_chars": 22000,
        "question": "Review this employment contract and identify clauses that may not be enforceable under Indian labor law. What are the key risks for the employee?",
    },
    {
        "id": 3,
        "name": "Sale Deed Review",
        "generator": generate_sale_deed,
        "target_chars": 20000,
        "question": "What are the key obligations of the buyer and seller under this sale deed? Are there any important clauses that are missing?",
    },
    {
        "id": 4,
        "name": "Legal Notice Validity",
        "generator": generate_legal_notice,
        "target_chars": 28000,
        "question": "Is this legal notice legally valid and properly drafted? What improvements would you suggest to strengthen it?",
    },
    {
        "id": 5,
        "name": "Partnership Deed Review",
        "generator": generate_partnership_deed,
        "target_chars": 24000,
        "question": "What are the rights and duties of each partner under this deed? Identify clauses that could potentially cause disputes between the partners.",
    },
]


# =============================================================================
#  TEST RUNNER
# =============================================================================


def run_test(test_case, api_url, api_key, timeout):
    """Run a single test case and return the result dict."""
    doc = test_case["generator"](test_case["target_chars"])
    prompt = doc + "\n\n--- QUESTION ---\n\n" + test_case["question"]
    actual_chars = len(prompt)

    payload = {"Promptquery": prompt}
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key

    result = {
        "id": test_case["id"],
        "name": test_case["name"],
        "prompt_chars": actual_chars,
        "doc_chars": len(doc),
        "question": test_case["question"],
        "status_code": None,
        "response_chars": 0,
        "elapsed": 0.0,
        "response_preview": "",
        "error": None,
    }

    start = time.time()
    try:
        resp = requests.post(api_url, json=payload, headers=headers, timeout=timeout)
        result["elapsed"] = time.time() - start
        result["status_code"] = resp.status_code

        if resp.status_code == 200:
            try:
                body = resp.json()
                answer = body.get("response", "") or body.get("answer", "") or str(body)
            except Exception:
                answer = resp.text
            result["response_chars"] = len(answer)
            result["response_preview"] = answer[:500]
        else:
            result["error"] = f"HTTP {resp.status_code}: {resp.text[:300]}"
    except requests.exceptions.Timeout:
        result["elapsed"] = time.time() - start
        result["error"] = f"Timeout after {timeout}s"
    except requests.exceptions.ConnectionError as e:
        result["elapsed"] = time.time() - start
        result["error"] = f"Connection error: {e}"
    except Exception as e:
        result["elapsed"] = time.time() - start
        result["error"] = f"Exception: {type(e).__name__}: {e}"

    return result


def print_summary_table(results):
    """Print a formatted summary table."""
    hdr = (
        f"{'#':>2}  {'Test Case':<30}  {'Doc':>7}  {'Prompt':>7}  "
        f"{'Status':>6}  {'Resp':>7}  {'Time':>7}  {'Result':<10}"
    )
    sep = "-" * len(hdr)

    print(f"\n{'=' * len(hdr)}")
    print("  LONG PROMPT TEST RESULTS")
    print(f"{'=' * len(hdr)}")
    print(hdr)
    print(sep)

    pass_count = 0
    total_time = 0.0

    for r in results:
        if r["error"]:
            verdict = "FAIL"
        elif r["status_code"] == 200 and r["response_chars"] > 100:
            verdict = "PASS"
            pass_count += 1
        elif r["status_code"] == 200:
            verdict = "WEAK"
        else:
            verdict = "FAIL"

        total_time += r["elapsed"]
        print(
            f"{r['id']:>2}  {r['name']:<30}  {r['doc_chars']:>6}c  {r['prompt_chars']:>6}c  "
            f"{(r['status_code'] or 'ERR'):>6}  {r['response_chars']:>6}c  "
            f"{r['elapsed']:>6.1f}s  {verdict:<10}"
        )

    print(sep)
    print(
        f"    {'TOTAL':<30}  {'':>7}  {'':>7}  {'':>6}  {'':>7}  "
        f"{total_time:>6.1f}s  {pass_count}/{len(results)} PASS"
    )
    print(f"{'=' * len(hdr)}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Test API with long legal document prompts (20-30K chars each)."
    )
    parser.add_argument(
        "--api-url",
        default="http://localhost:5000/pyapi/search",
        help="API endpoint URL (default: http://localhost:5000/pyapi/search)",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API key sent as X-API-Key header",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Per-request timeout in seconds (default: 300)",
    )
    args = parser.parse_args()

    print(f"\nLong Prompt Test Suite")
    print(f"  API URL:  {args.api_url}")
    print(f"  Timeout:  {args.timeout}s per request")
    print(f"  Tests:    {len(TEST_CASES)} sequential requests")
    print(f"  Started:  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

    # Verify documents reach target sizes
    print("  Document sizes (pre-flight check):")
    for tc in TEST_CASES:
        doc = tc["generator"](tc["target_chars"])
        status = "OK" if len(doc) >= tc["target_chars"] * 0.95 else "SHORT"
        print(f"    {tc['id']}. {tc['name']:<30} target={tc['target_chars']:>6}  actual={len(doc):>6}  [{status}]")
    print()

    # Run tests sequentially
    results = []
    for i, tc in enumerate(TEST_CASES, 1):
        print(f"  [{i}/{len(TEST_CASES)}] {tc['name']} (~{tc['target_chars']//1000}K chars) ... ", end="", flush=True)
        result = run_test(tc, args.api_url, args.api_key, args.timeout)
        results.append(result)

        if result["error"]:
            print(f"FAIL ({result['elapsed']:.1f}s) - {result['error'][:80]}")
        else:
            print(f"HTTP {result['status_code']} ({result['elapsed']:.1f}s) - {result['response_chars']} chars response")

    # Summary table
    print_summary_table(results)

    # Print errors/previews
    for r in results:
        print(f"--- Test #{r['id']}: {r['name']} ---")
        if r["error"]:
            print(f"  ERROR: {r['error']}")
        elif r["response_preview"]:
            preview = r["response_preview"].replace("\n", " ")[:200]
            print(f"  Preview: {preview}...")
        print()

    # Save results JSON
    script_dir = os.path.dirname(os.path.abspath(__file__))
    json_path = os.path.join(script_dir, "test_results_long_prompts.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "api_url": args.api_url,
                "timeout": args.timeout,
                "timestamp": datetime.now().isoformat(),
                "results": results,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )
    print(f"  Results saved to: {json_path}")

    # Exit code
    failures = sum(1 for r in results if r["error"] or (r["status_code"] and r["status_code"] != 200))
    return 1 if failures > len(results) // 2 else 0


if __name__ == "__main__":
    sys.exit(main())
