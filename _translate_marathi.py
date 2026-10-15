"""
Glossary-based Marathi->English(India) conversion for IGR Index II text.
Dictionary substitution (longest phrase first), NOT a generic MT model --
built from the actual recurring vocabulary in Maharashtra property-deed
Index II extracts (deed types, floor/building/area labels, rent/deposit
terms, survey/CTS numbers). Whatever Devanagari text remains after
substitution is wrapped in [[ ]] so it can be found and hand-translated
(mostly personal names and specific building/street names, which a
dictionary can't generalize).
"""
import re

DEED_TYPES = {
    "36-अ-लिव्ह अॅड लायसन्सेस": "Leave and License Agreement (Section 36-A)",
    "36 - अ - लिव्ह अॅण्ड लायसन्सेस": "Leave and License Agreement (Section 36-A)",
    "65-चुक दुरुस्ती पत्र": "Rectification Deed (Section 65)",
    "अँग्रीमेंट टू सेल": "Agreement to Sell",
    "असाईनमेंट डीड": "Assignment Deed",
    "करारनामा": "Agreement",
    "कार्यकंत्राट": "Work Contract",
    "गहाणखत": "Mortgage Deed",
    "ट्रान्सफर डीड": "Transfer Deed",
    "पर्यायी जागेचा करार": "Alternate Premises Agreement",
    "पार्टीशन": "Partition Deed",
    "बक्षीसपत्र": "Gift Deed",
    "भाडेपट्टा": "Lease Deed",
    "भाडेपट्ट्याचे हस्तांतरणपत्र": "Lease Assignment Deed",
    "मॉरगेज डीड": "Mortgage Deed",
    "रद्दलेख": "Cancellation Deed",
    "रिलीज डीड": "Release Deed",
    "सेल डीड": "Sale Deed",
}

# Longest/most specific phrases first -- substitution is greedy and must not
# let a short word inside a longer phrase match before the phrase itself.
PHRASES = {
    # Common place names -- VERY high frequency across every row, so these
    # get hand-picked dictionary entries rather than being left to the
    # unidecode fallback (which rendered "मुंबई" as the unreadable
    # "muNbii[auto]" instead of "Mumbai").
    "मुंबई शहर": "Mumbai City", "मुंबई": "Mumbai", "महाराष्ट्र": "Maharashtra",
    "परेल": "Parel", "परेळ": "Parel", "लोअर परेल": "Lower Parel", "लोवर परेल": "Lower Parel",
    "वरळी": "Worli", "दादर": "Dadar", "कुर्ला पश्चिम": "Kurla West", "कुर्ला वेस्ट": "Kurla West",
    "कुर्ला": "Kurla", "अंधेरी पूर्व": "Andheri East", "अंधेरी": "Andheri", "ठाणे वेस्ट": "Thane West",
    "ठाणे पश्चिम": "Thane West", "ठाणे": "Thane", "पुणे": "Pune", "नवी मुंबई": "Navi Mumbai",
    "कोलकता": "Kolkata", "कोलकत्ता": "Kolkata", "वाशी": "Vashi", "नाशिक": "Nashik",
    "दहिसर": "Dahisar", "भोईवाडा": "Bhoiwada", "काळाचौकी": "Kalachowki", "शिवडी पश्चिम": "Sewri West",
    "शिवडी": "Sewri", "प्रभादेवी": "Prabhadevi", "कोले कल्याण": "Kole Kalyan", "कोलेकल्याण": "Kole Kalyan",
    "माहीम": "Mahim", "एलफिस्टन स्टेशन": "Elphinstone Station", "एलफिस्टन": "Elphinstone",
    "डिलायल रोड": "Delisle Road", "घोरपडी": "Ghorpadi", "मालाड वेस्ट": "Malad West", "मालाड": "Malad",
    "बोरीवली": "Borivali", "घाटकोपर": "Ghatkopar", "बांद्रा कुर्ला कॉम्प्लेक्स": "Bandra Kurla Complex",
    "बांद्रा पूर्व": "Bandra East", "बांद्रा(पूर्व)": "Bandra (East)", "बांद्रा": "Bandra",
    "सोलापूर": "Solapur", "अहमदाबाद": "Ahmedabad",

    # Header labels
    "सह दु.नि.": "Joint Sub-Registrar ",
    "सह दुय्यम निबंधक": "Joint Sub-Registrar",
    "दुय्यम निबंधक": "Sub-Registrar",
    "दस्त क्रमांक": "Document Number",
    "नोदंणी": "Registration",
    "गाव": "Village",
    "सूची क्र.2": "Index No. 2",
    "सूची क्र 2": "Index No. 2",
    "Generated Through eSearch Module,For original report please contact concern SRO office.":
        "Generated Through eSearch Module, for the original report please contact the concerned SRO office.",
    "Only For View Purpose": "Only For View Purpose",

    # Numbered-field labels (Index II's own (1)-(14))
    "विलेखाचा प्रकार": "Type of Deed",
    "मोबदला": "Consideration",
    "बाजारभाव(भाडेपटटयाच्या बाबतितपटटाकार आकारणी देतो की पटटेदार ते नमुद करावे)":
        "Market Value (in case of lease, state whether the lessor or lessee bears the assessment)",
    "बाजारभाव": "Market Value",
    "भू-मापन,पोटहिस्सा व घरक्रमांक(असल्यास)": "Survey Number, Sub-division and House Number (if any)",
    "क्षेत्रफळ": "Area",
    "आकारणी किंवा जुडी देण्यात असेल तेव्हा": "Assessment or Judi, when given",
    "दस्तऐवज करुन देणा-या/लिहून ठेवणा-या पक्षकाराचे नाव किंवा दिवाणी न्यायालयाचा हुकुमनामा किंवा आदेश असल्यास,प्रतिवादिचे नाव व पत्ता":
        "Name of the Executant Party, or name and address of the respondent if there is a civil court decree or order",
    "दस्तऐवज करुन घेणा-या पक्षकाराचे व किंवा दिवाणी न्यायालयाचा हुकुमनामा किंवा आदेश असल्यास,प्रतिवादिचे नाव व पत्ता":
        "Name of the Claimant Party, or name and address of the respondent if there is a civil court decree or order",
    "दस्तऐवज करुन दिल्याचा दिनांक": "Date of Execution",
    "दस्त नोंदणी केल्याचा दिनांक": "Date of Registration",
    "अनुक्रमांक,खंड व पृष्ठ": "Serial Number, Volume and Page",
    "बाजारभावाप्रमाणे मुद्रांक शुल्क": "Stamp Duty as per Market Value",
    "बाजारभावाप्रमाणे नोंदणी शुल्क": "Registration Fee as per Market Value",
    "शेरा": "Remarks",
    "मुल्यांकनासाठी विचारात घेतलेला तपशील:-": "Details considered for valuation:-",
    "मुल्यांकनाची आवश्यकता नाही कारण द्स्तप्रकारनुसार आवश्यक नाही कारणाचा तपशील द्स्तप्रकारनुसार आवश्यक नाही":
        "Valuation not required as not necessary for this document type; reason details not required for this document type",
    "मुद्रांक शुल्क आकारताना निवडलेला अनुच्छेद :-": "Article selected while levying stamp duty:-",
    "When possession is not given": "When possession is not given",

    # Party-block labels
    "नाव:-": "Name:- ",
    "वय:-": "Age:- ",
    "पत्ता:-": "Address:- ",
    "पिन कोड:-": "Pin Code:- ",
    "पॅन नं:-": "PAN No.:- ",
    "तर्फे": "through",

    # Property-description labels
    "पालिकेचे नाव": "Municipality Name",
    "इतर वर्णन": "Other Description",
    "इतर माहिती": "Other Information",
    "सदनिका नं": "Flat No.",
    "सदनिका क्र": "Flat No.",
    "फ्लॅट नं": "Flat No.",
    "फ़्लत नं": "Flat No.",
    "रूम नं": "Room No.",
    "रूम अन्डर स्टेर्केसीट": "Room Under Staircase",
    "शॉप नं": "Shop No.",
    "माळा नं": "Floor No.",
    "ग्रौंड फ़्लोर": "Ground Floor",
    "तळ मजला": "Ground Floor",
    "तळमजला": "Ground Floor",
    "तळ मजल्यावरील": "On the Ground Floor",
    "मजला": "Floor",
    "वा मजला": "th Floor",
    "वा मजल्यावर": "th Floor",
    "इमारतीचे नाव": "Building Name",
    "व्यावसायिक ईमारत": "Commercial Building",
    "ब्लॉक नं": "Block No.",
    "रोड नं": "Road No.",
    "रोड": "Road",
    "विंग": "Wing",
    "जोडी बी विंग": "combined B Wing",
    "सोबत": "along with",
    "कार पार्किंग": "car parking",
    "कार पर्किंग": "car parking",
    "कारपार्किंग": "car parking",
    "कार पार्कीग": "car parking",
    "मेकॅॅनिकल कारपार्किंग": "mechanical car parking",
    "टू विलरपार्किंग": "two-wheeler parking",
    "टू व्हीलर पार्किंग": "two-wheeler parking",
    "टू व्हिलर पार्किंग": "two-wheeler parking",
    "टू विल्लर पार्किंग": "two-wheeler parking",
    "चे क्षेत्रफळ": "having an area of",
    "एकूण क्षेत्र": "total area",
    "एकुण क्षेत्र": "total area",
    "एकूण एरिया": "total area",
    "क्षेत्र": "area",
    "चौ फूट": "sq. ft.",
    "चौ. फुट": "sq. ft.",
    "चौ फुट": "sq. ft.",
    "चौ-फूट": "sq. ft.",
    "चौ.फुट": "sq. ft.",
    "चौ.मी": "sq. m.",
    "चौ मी": "sq. m.",
    "चौ.मीटर": "sq. m.",
    "चौ.फूट": "sq. ft.",
    "स्केअर फिट": "sq. ft.",
    "स्केअर फुट": "sq. ft.",
    "स्केअर फीट": "sq. ft.",
    "कारपेट एरिया": "carpet area",
    "कार्पेट एरिया": "carpet area",
    "कारपेट": "carpet",
    "कार्पेट": "carpet",
    "बिल्टअप एरिया": "built-up area",
    "बिल्टअप": "built-up",
    "सेलेबल एरिया": "saleable area",
    "रेरा कारपेट": "RERA carpet",
    "मासिक भाडे": "monthly rent",
    "वार्षिक भाडे": "annual rent",
    "अनामत रक्कम": "deposit amount",
    "अमानत रक्कम": "deposit amount",
    "डीपॉझिट": "deposit",
    "डिपॉझिट": "deposit",
    "कालावधी": "duration",
    "एकूण कालावधी": "total duration",
    "एकुण कालावधि": "total duration",
    "महिने": "months",
    "महिन्या": "month(s)",
    "महिन्यांकरिता": "for month(s)",
    "वर्षा करिता": "per year",
    "वर्षाकरिता": "per year",
    "वर्षी": "year",
    "पहिल्या": "1st",
    "दुसऱ्या": "2nd",
    "दुसऱ्या वर्षी": "2nd year",
    "तिसऱ्या": "3rd",
    "चौथ्या": "4th",
    "चौथ्यावर्षी": "4th year",
    "पाचव्या": "5th",
    "निरंक": "nil",
    "रुपये": "Rs.",
    "रु.": "Rs.",
    "रु": "Rs.",
    "दिनांक": "dated",
    "मौजे": "Locality",
    "विलेज": "Village",
    "व्हिलेज": "Village",
    "तालुका": "Taluka",
    "जिल्हा": "District",
    "प्लॉट नं": "Plot No.",
    "प्लॉट नंबर": "Plot No.",
    "फायनल प्लॉट नं": "Final Plot No.",
    "सर्वे नं": "Survey No.",
    "सी एस नं": "C.S. No.",
    "सीटीएस नंबर": "CTS No.",
    "सी टी एस नं": "C.T.S. No.",
    "हिस्सा नं": "Hissa (Sub-division) No.",
    "पार्ट": "Part",
    "नाते": "Relationship",
    "आई वडील व मुलगी": "parents and daughter",
    "आजोबा व नातू": "grandfather and grandson",
    "देणार व घेणार": "transferor and transferee",
    "देणार": "Transferor",
    "घेणार": "Transferee",
    "हिस्सा ट्रान्सफर": "share transferred",
    "अविभाजित हिस्सा": "undivided share",
    "विनामोबदला बक्षीस": "gifted without consideration",
    "जुन्या सदनिकेच्या बदल्यात नवीन सदनिका": "new flat in exchange for the old flat",
    "कर्जाची रक्कम": "loan amount",
    "दस्तात नमूद केल्याप्रमाणे": "as stated in the document",
    "दस्तात नमुद केल्याप्रमाणे": "as stated in the document",
    "दस्तात नमूद केल्या प्रमाणे": "as stated in the document",
    "बाकी दस्तात नमूद केल्या प्रमाणे": "remaining as stated in the document",
    "इतर सर्व माहिती व मिळकतीचे वर्णन दस्तात नमूद केल्याप्रमाणे": "all other information and description of the property as stated in the document",
    "इतर माहिती व मिळकती चे सर्व वर्णन दस्तात नमूद केल्याप्रमाणे": "other information and the full description of the property as stated in the document",
    "इत्तर सर्व माहिती व मिळकतिचे वर्णन दस्तात नमूद केल्याप्रमाणे": "all other information and description of the property as stated in the document",
    "चूक दुरुस्ती पत्र": "Rectification Deed",
    "रद्द लेख": "Cancellation Deed",
    "परत करण्यायोग्य सिक्युरिटी डिपॉझिट": "Refundable Security Deposit",
}


# Hand-translated by reading each one directly (not auto-transliterated) --
# every distinct party name found in executant_party/claimant_party across
# the 66-row 2020-2026 Parel/Property No. 10 dataset.
PARTY_NAMES = {
    "किशोर शिवशंकर वैष्णव ......... 2 नं मुलगा": "Kishore Shivshankar Vaishnav ......... 2nd son",
    "तरुण सेठी": "Tarun Sethi",
    "तुषार रतिलाल वोरा": "Tushar Ratilal Vora",
    "दिलीप शिवशंकर वैष्णव ............ लास्ट नं मुलगा": "Dilip Shivshankar Vaishnav ............ last son",
    "पूर्वी तुषार वोरा": "formerly Tushar Vora",
    "मनोज शिवशंकर वैष्णव .......... मोठा मुलगा": "Manoj Shivshankar Vaishnav .......... eldest son",
    "शिवशंकर माथुरदास वैष्णव .... वडिल": "Shivshankar Mathurdas Vaishnav .... father",
    "हौसिंग डेव्हलपर्स फायनान्स कारर्पोरेशन ल‍िमीटेड": "Housing Developers Finance Corporation Limited",
    "अनितादेवी आशोककुमार मेहता": "Anitadevi Ashokkumar Mehta",
    "अनिल सुरेश मालप": "Anil Suresh Malap",
    "अमित सुरेंद्र सिंगले": "Amit Surendra Singale",
    "अर्पणा सेहगल": "Arpana Sehgal",
    "अ‍िव्हेट पी डिसोजा": "Ivet P. D'Souza",
    "आशीष गयाप्रसाद गुप्ता": "Ashish Gayaprasad Gupta",
    "इंडस टॉवर्स लि. चे लीगल हेड श्री पुनीत शुक्ला तर्फे कु. मु . म्हणून विनोद पी आहुजा":
        "Indus Towers Ltd.'s Legal Head Shri Puneet Shukla, through Power of Attorney holder Vinod P. Ahuja",
    "उर्वशी वी. दत्तवाणी": "Urvashi V. Dattwani",
    "उषा वेंकेटेश्वरनं": "Usha Venkateswaran",
    "ऍक्सिस बँक लिमिटेड चे व्हाईस प्रेसिडेंट व परेल ब्रांच हेड स्वरूप - गोंसाल्वीस":
        "Axis Bank Limited's Vice President and Parel Branch Head Swaroop Gonsalvis",
    "ऍग्नल फर्नांडीज़": "Agnel Fernandez",
    "एम एस लिओ इस्टॅट्स प्रायव्हेट लिमिटेड चे भागीदार संजय भवरलाल जैन": "M/s Leo Estates Private Limited's Partner Sanjay Bhavarlal Jain",
    "ऐन्ड्रू उबाल्डो गोणसालवीस": "Andrew Ubaldo Gonsalves",
    "ओमकार विजय काटकर": "Omkar Vijay Katkar",
    "कन्यादेवी शिवशंकर वैष्णव...आई": "Kanyadevi Shivshankar Vaishnav ... mother",
    "कमलेश कुमार शारदाप्रसाद दुबे": "Kamlesh Kumar Sharadaprasad Dubey",
    "कम्फर्ट ग्रिड टेक्नॉलॉजीज प्रायव्हेट लिमिटेड चे ऑथो सिग्नटरी तन्मय कुंटे":
        "Comfort Grid Technologies Private Limited's Authorized Signatory Tanmay Kunte",
    "कर्ज घेणार - नॅशनल रियल्टी प्रा. लिमिटेड चे प्राधिकृत व्यक्ती सुम‍ित पाटील":
        "Borrower - National Realty Pvt. Ltd.'s Authorized Person Sumit Patil",
    "कर्ज घेणार - नॅशनल रियल्टी प्रा. लिमिटेड चे संचालक सोरभ जैन": "Borrower - National Realty Pvt. Ltd.'s Director Saurabh Jain",
    "कविता कदान तर्फे कु मु किशोर गुलाबानी": "Kavita Kadan, through Power of Attorney holder Kishore Gulabani",
    "काजल केतन कुमार संघवी": "Kajal Ketan Kumar Sanghvi",
    "कामत ब्रधर्झ तर्फे प्रोप्रा दिपक कामत": "Kamat Brothers, through Proprietor Deepak Kamat",
    "कार्सन मायकल स्कुइरेस": "Carson Michael Squires",
    "कुणाल गुप्ता": "Kunal Gupta",
    "कॅपिटल इंडिया होम लोन्स लिमिटेड तर्फे सही करणारे गणेश उत्तम ढेंबरे":
        "Capital India Home Loans Limited, through Signatory Ganesh Uttam Dhembare",
    "कॅमडेन रिअल्टी प्रायव्हेट लिमिटेड चे डायरेक्टर जिग्नेश संघवी": "Camden Realty Private Limited's Director Jignesh Sanghvi",
    "गायत्री विश्वेश वागळे": "Gayatri Vishwesh Wagle",
    "गीता नवीन चढा": "Geeta Navin Chadha",
    "गुरचरण सिंह कदान तर्फे कु मु किशोर गुलाबानी": "Gurcharan Singh Kadan, through Power of Attorney holder Kishore Gulabani",
    "गोपिका मोहित कपूर": "Gopika Mohit Kapoor",
    "गौतम सेहगल": "Gautam Sehgal",
    "चारु टंडन तर्फे कु मु म्हणून गौरांग लखोटिया": "Charu Tandon, through Power of Attorney holder Gaurang Lakhotia",
    "जगमोहन कपूर": "Jagmohan Kapoor",
    "जतिन रविंद्र शाह": "Jatin Ravindra Shah",
    "जयेश शांतीलाल शाह": "Jayesh Shantilal Shah",
    "ज्ञानदीप को ऑपरेटिव्ह क्रेडिट सोसायटी लिमिटेड तर्फे मॅनेजर अजय जाधव": "Dnyandeep Co-operative Credit Society Limited, through Manager Ajay Jadhav",
    "ज्योती विजय काटकर": "Jyoti Vijay Katkar",
    "डॅनी फ़िरोज़ हंसोटिया": "Danny Feroz Hansotia",
    "तवलीन सिंह": "Tavleen Singh",
    "दि पारसी पंचायत फंड्स एंड प्रोपर्टीस तर्फे रॉनी पटेल": "The Parsi Panchayat Funds and Properties, through Ronnie Patel",
    "दी बृहनमुंबई इलेक्ट्रिक सप्लाई अँड ट्रॅनसपॉर्ट अन्डर्टेकींग तर्फे कुलमुखत्यार देवेंद्र जावकर":
        "The Brihanmumbai Electric Supply and Transport Undertaking, through General Power of Attorney holder Devendra Jawkar",
    "नवीन महेंद्र चढा": "Navin Mahendra Chadha",
    "नारायण उद्योग ओनर्स प्रिमाईसेस कॉ ओप्प सोसायटी लिमिटेड तर्फे मॅनेजर श्री पंकज पाटील":
        "Narayan Udyog Owners Premises Co-op Society Limited, through Manager Shri Pankaj Patil",
    "नितिन टंडन तर्फे कु मु म्हणून गौरांग लखोटिया": "Nitin Tandon, through Power of Attorney holder Gaurang Lakhotia",
    "नॅशनल रियल्टी प्रा. लिमिटेड चे संचालक सैारभ जैन": "National Realty Pvt. Ltd.'s Director Saurabh Jain",
    "नॅशनल रियल्टी प्रा. लिमिटेड चे संचालक सौरभ जैन": "National Realty Pvt. Ltd.'s Director Saurabh Jain",
    "परव बी. कारिया": "Parav B. Karia",
    "पराज मुकेश छेडा यांच्यातर्फे कुलमुखत्यार पियुष कोठारी": "Paraj Mukesh Cheda, through General Power of Attorney holder Piyush Kothari",
    "पल्लवी उल्हास चव्हाण": "Pallavi Ulhas Chavan",
    "पियुष सुराणा": "Piyush Surana",
    "पोदार एज्युकेशन प्रायव्हेट लिमिटेड तर्फे मॅनेजर ऑथोराईज सिग्नेटरी अजय वीर गौर":
        "Podar Education Private Limited, through Manager Authorized Signatory Ajay Veer Gaur",
    "प्रज्ञा रामचंद्र खेडेकर": "Pragya Ramchandra Khedekar",
    "प्रप्ती प्रदीप पेडनेकर पूर्वाश्रमीची रेशमा रामचंद्र खेडेकर": "Prapti Pradeep Pednekar, formerly Reshma Ramchandra Khedekar",
    "प्लास्टोफ्लेक्स इंडस्ट्रीज चे भागीदार दिव्येश पारीख": "Plastoflex Industries' Partner Divyesh Parikh",
    "फॅनी जुड कुटिन्हो": "Fanny Jude Coutinho",
    "बँक ऑफ बडोदा तर्फे अॅथो. सिग्नेटरी न‍ितीन भुजबळ": "Bank of Baroda, through Authorized Signatory Nitin Bhujbal",
    "बनवारीलाल कंदोई एचयुएफ चे कर्ता बनवारीलाला कंदोई तर्फे कु.मु. किरण विनोदकुमार कंदोई":
        "Banwarilal Kandoi HUF's Karta Banwarilal Kandoi, through Power of Attorney holder Kiran Vinodkumar Kandoi",
    "बॅंक ऑफ बडोदा तर्फे अधिकृत सही करणार नितीन भुजबळ": "Bank of Baroda, through Authorized Signatory Nitin Bhujbal",
    "भाउसाहेब अप्पासाहेब रायजाधव": "Bhausaheb Appasaheb Rajadhav",
    "भानुमती शांतीलाल शाह": "Bhanumati Shantilal Shah",
    "भूषण एम. कारिया": "Bhushan M. Karia",
    "मनाली राहुल नाईक": "Manali Rahul Naik",
    "महाराष्ट्र इस्टेट रोड डेव्हलपमेंट कॉर्पोरेशन लि. तर्फे जॉईन्ट मॅनेजिंग डायरेक्टर (2) श्री. कैलास व्ही. जाधव":
        "Maharashtra Estate Road Development Corporation Ltd., through Joint Managing Director (2) Shri Kailas V. Jadhav",
    "मिडटाउन डायग्नोस्टिक प्रायवेट लिमिटेड तर्फे डायरेक्टर डॉ.सचिन काशीराव देशमुख":
        "Midtown Diagnostic Private Limited, through Director Dr. Sachin Kashirao Deshmukh",
    "मिनादेवी अनिलकुमार मेहता": "Minadevi Anilkumar Mehta",
    "मीनाक्षी वेंकेटेश्वरनं": "Meenakshi Venkateswaran",
    "मुकेश रुपशी छेडा यांच्यातर्फे कुलमुखत्यार पियुष कोठारी": "Mukesh Rupshi Cheda, through General Power of Attorney holder Piyush Kothari",
    "मे ज्युलिएट अँपरेल्स प्रा लि ( पूर्वी ज्ञात म्हणून ज्युलिएट अँपरेल्स लि ) तर्फे अधिकृत अधिकारी अजित जयंतीलाल पांचाल":
        "M/s Juliet Apparels Pvt. Ltd. (formerly known as Juliet Apparels Ltd.), through Authorized Officer Ajit Jayantilal Panchal",
    "मे ज्युलिएट अँपरेल्स प्रा लि ( पूर्वी ज्ञात म्हणून ज्युलिएट अँपरेल्स लि ) तर्फे अधिकृत अधिकारी अज‍ित जयंतीलाल पंचाळ":
        "M/s Juliet Apparels Pvt. Ltd. (formerly known as Juliet Apparels Ltd.), through Authorized Officer Ajit Jayantilal Panchal",
    "मे. व्ही पी अँड सन तर्फे भाग‍िदार सुनील प्रकाशचंद सिरोया": "M/s V. P. & Son, through Partner Sunil Prakashchand Siroya",
    "मे. शायना लिंगू बिडीवाला आणि कं तर्फे ऑथोराइज्ड सिग्नेटरी भाविक किरण शाह":
        "M/s Shayna Lingu Bidiwala and Co., through Authorized Signatory Bhavik Kiran Shah",
    "मेसर्स अनन्या एंटरप्रायझेस चे भागीदार मॅरिगोल्ड इन्व्हेस्ट्रेड प्रा. लि, चे संचालक शीतल एस नाईक":
        "M/s Ananya Enterprises' Partner Marigold Investrade Pvt. Ltd.'s Director Sheetal S. Naik",
    "मेसर्स ओर्समन क्रेडिट प्रा. लि. चे ऑथोराइज पर्सन मनीष चंद पांडे": "M/s Orsman Credit Pvt. Ltd.'s Authorized Person Manish Chand Pandey",
    "मेसर्स दिरा इंटरप्रायजेस तर्फे प्रोप्रायटर पुनीत भरत जैन": "M/s Dira Enterprises, through Proprietor Puneet Bharat Jain",
    "मेसर्स प्रोएक्टिव्ह इन आणि आउट अॅ डव्हर्टायझिंग प्रा. लि तर्फे ऑथोराईज सिग्नेटरी ज्योती पिल्लाई":
        "M/s Proactive In and Out Advertising Pvt. Ltd., through Authorized Signatory Jyoti Pillai",
    "मेसर्स मर्क्युरी जेम्स प्रा. लिमिटेड चे ऑथोराइज पर्सन अनिल सेकसरिया": "M/s Mercury Gems Pvt. Ltd.'s Authorized Person Anil Seksaria",
    "मेसर्स रेनैसान्स होम्स चे भागीदार अनिल विजय देशमुख": "M/s Renaissance Homes' Partner Anil Vijay Deshmukh",
    "मेसर्स व्हाईट अँड ब्रिफ ऍडव्होकेटस अँड सॉलिसिटर्स एलएलपी तर्फे प्राधिकृत व्यक्ती प्रफुल मालेकर":
        "M/s White & Brief Advocates and Solicitors LLP, through Authorized Person Praful Malekar",
    "मेसर्स. बालाजी इम्पेक्स तर्फे भागीदार इलेश अमृतलाल गढिया": "M/s Balaji Impex, through Partner Ilesh Amrutlal Gadhia",
    "मोहम्मद जुनैद इकबाल सेलिया": "Mohammed Junaid Iqbal Seliya",
    "मोहम्मद जुबेर इकबाल सेलिया": "Mohammed Zuber Iqbal Seliya",
    "मोहित राकेश कपूर तर्फे मुखत्यार गोपिका मोहित कपूर": "Mohit Rakesh Kapoor, through Power of Attorney holder Gopika Mohit Kapoor",
    "मोहित वेदप्रकाश अग्रवाल": "Mohit Vedprakash Agrawal",
    "यश प्रशांत रायजाधव": "Yash Prashant Rajadhav",
    "यशोदा नामदेव गुरव": "Yashoda Namdeo Gurav",
    "रजनी गुप्ता": "Rajani Gupta",
    "रविंद्र चिनूभाई शाह (एच यु एफ) यांच्यावतीने कर्ता म्हणून जयेश रविंद्र शाह":
        "Ravindra Chinubhai Shah (HUF), on its behalf as Karta Jayesh Ravindra Shah",
    "रवींद्र ज्ञानेश्वर पाटसकर": "Ravindra Dnyaneshwar Patsakar",
    "राजू गोविंद पवार": "Raju Govind Pawar",
    "राजेंद्र प्रभाकर जळगावकर": "Rajendra Prabhakar Jalgaonkar",
    "राजेश चिंतामण सावंत": "Rajesh Chintaman Sawant",
    "राहुल प्रभाकर नाईक": "Rahul Prabhakar Naik",
    "राहुल विनोद कजानिया": "Rahul Vinod Kajania",
    "रिचा रिअल्टर्स प्रायव्हेट लिमिटेडचे डायरेक्टर प्रकाश शरद जोशी तर्फे मुखत्यार राजेंद्र सुपल":
        "Richa Realtors Private Limited's Director Prakash Sharad Joshi, through Power of Attorney holder Rajendra Supal",
    "रितेश सत्यविजय गोळवणकर": "Ritesh Satyavijay Golwankar",
    "रिबेल फुड्स प्रा.लि. तर्फे अधिकृत स्वाक्षरीकार अभिजीत लहू शिंदे": "Rebel Foods Pvt. Ltd., through Authorized Signatory Abhijeet Lahu Shinde",
    "रुखसाना अयाज पटेल": "Ruksana Ayaz Patel",
    "लक्ष्मीनारायण मल्लेशाम गुंड्लापेल्ली तर्फे कुलमुखत्यार म्हणून राजेश चिंतामण सावंत":
        "Lakshminarayan Malleshyam Gundlapally, through General Power of Attorney holder Rajesh Chintaman Sawant",
    "लक्ष्मीनारायण मल्लेशाम गुंड्लापेल्ली तर्फे मुखत्यार राजेश चिंतामण सावंत":
        "Lakshminarayan Malleshyam Gundlapally, through Power of Attorney holder Rajesh Chintaman Sawant",
    "लायोनल हर्बट डीसोजा तर्फे मुखत्यार अ‍िव्हेट पी डिसोजा": "Lionel Herbert D'Souza, through Power of Attorney holder Ivet P. D'Souza",
    "लिवलीट कॅम्युनिटी एलएलपी चे भागीदार मयंक बजाज": "Livlit Community LLP's Partner Mayank Bajaj",
    "लेनेसिस फूड वर्क प्रा लि तर्फे अभिषेक आनंद": "Lenesis Food Work Pvt. Ltd., through Abhishek Anand",
    "विक्रम गयाप्रसाद गुप्ता": "Vikram Gayaprasad Gupta",
    "विजय चंद्रकांत देसाई": "Vijay Chandrakant Desai",
    "विजय टी दत्तवाणी": "Vijay T. Dattwani",
    "विजय नामदेव गुरव": "Vijay Namdeo Gurav",
    "विजय मुकुंद काटकर": "Vijay Mukund Katkar",
    "विवेक कंदोई": "Vivek Kandoi",
    "विशाल गुप्ता": "Vishal Gupta",
    "विशाल प्रकाशचंद जैन": "Vishal Prakashchand Jain",
    "विश्वेश कुमार वागळे": "Vishwesh Kumar Wagle",
    "वेदप्रकाश कालीचरण अग्रवाल": "Vedprakash Kalicharan Agrawal",
    "वेलनेस फॉरेव्हर मेडीकेर प्राएवेट लिमिटेड तर्फे प्रदिकृत व्यक्ती अंगत देसाई":
        "Wellness Forever Medicare Private Limited, through Authorized Person Angat Desai",
    "शांतनु श्याम": "Shantanu Shyam",
    "शालिनी अमित सिंगले": "Shalini Amit Singale",
    "शिखा अनुज श्रीवास्तवा": "Shikha Anuj Shrivastava",
    "शीला सुरेश मालप": "Sheela Suresh Malap",
    "शेरलीन फर्नांडीस": "Sherlyn Fernandes",
    "शैलेश रामचंद्र खेडेकर": "Shailesh Ramchandra Khedekar",
    "श्रद्धा विजय समजीसकर": "Shraddha Vijay Samjiskar",
    "श्रीपती बिल्ड इन्फ्रा इन्व्हेस्टमेंट लिमिटेड चे संचालक तापस आर चतुर्वेदी संतोष एस पोयरेकर":
        "Shripati Build Infra Investment Limited's Directors Tapas R. Chaturvedi, Santosh S. Poyrekar",
    "संगीता राजू पवार": "Sangeeta Raju Pawar",
    "संचित सुनेजा": "Sanchit Suneja",
    "सचिन सुरेश मालप": "Sachin Suresh Malap",
    "समीर रामचंद्र खेडेकर": "Sameer Ramchandra Khedekar",
    "सांतनु श्याम": "Shantanu Shyam",
    "साईकृपा सहकारी पतपेढी मर्यादित तर्फे सुदाम सुतार": "Saikrupa Sahakari Patpedhi Maryadit (Co-op. Credit Society Ltd.), through Sudam Sutar",
    "सायली विजय देसाई": "Sayali Vijay Desai",
    "सी एस .वेंकेटेश्वरनं": "C. S. Venkateswaran",
    "सुगंधा रामचंद्र खेडेकर": "Sugandha Ramchandra Khedekar",
    "सुचिता पांडुरंग सुकाळे": "Suchita Pandurang Sukale",
    "सुप्रिया सुधीर सावंत": "Supriya Sudhir Sawant",
    "सुरेंद्र ज्ञानेश्वर सोनावणे": "Surendra Dnyaneshwar Sonawane",
    "सुशमा मिलिंद कदम पूर्वाश्रमीची सुशमा रामचंद्र खेडेकर": "Sushma Milind Kadam, formerly Sushma Ramchandra Khedekar",
    "स्पोर्ट्समेड मुंबई एलएलपी चे भागीदार डॉ. मीरा अनंत जोशी": "Sportsmed Mumbai LLP's Partner Dr. Meera Anant Joshi",
    "हाऊसिंग डेव्हलपमेंट फायनान्स कॉर्पोरेशन लिमिटेड": "Housing Development Finance Corporation Limited",
    "हाऊसिंग डेव्हलपमेंट फायनान्स कॉर्पोरेशन लिमिटेड चे ---------------------": "Housing Development Finance Corporation Limited's ---------------------",
    "हिरानंदानी फायनान्सिअल सर्विस प्रायवेट लिमिटेड तर्फे ऑथोराईज सिग्नेटरी तानाजी बाबु पाखरे":
        "Hirandani Financial Services Private Limited, through Authorized Signatory Tanaji Babu Pakhare",
    "हिरावती शारदाप्रसाद दुबे": "Hiravati Sharadaprasad Dubey",
}

# Common connecting/small words left over after the PHRASES pass -- closes
# most of the remaining gap on non-proper-noun text.
CONNECTORS = {
    "आणि": "and", "व": "and", "किंवा": "or", "असे": "such", "असलेल्या": "that are/is",
    "ऐवजी": "instead of", "सोबत": "along with", "साठी": "for", "करिता": "for", "म्हणून": "as",
    "यांच्यातर्फे": "on behalf of", "तर्फे": "through", "यांच्यावतीने": "on behalf of",
    "ते": "to", "पासून": "from", "वरील": "on/above", "खालील": "below", "नंतर": "after",
    "अगोदर": "before", "आता": "now", "सदर": "the said", "असून": "being",
    "यामध्ये": "in this", "त्यानुसार": "accordingly", "त्यामुळे": "due to this",
    "जवळ": "near", "समोर": "in front of", "मागे": "behind", "पश्चिम": "West",
    "पूर्व": "East", "उत्तर": "North", "दक्षिण": "South", "अंतर्गत": "under",
}


def _translate_segment(text: str) -> str:
    for phrase, eng in sorted(PHRASES.items(), key=lambda kv: -len(kv[0])):
        text = text.replace(phrase, eng)
    for phrase, eng in sorted(CONNECTORS.items(), key=lambda kv: -len(kv[0])):
        text = re.sub(rf"(?<![ऀ-ॿ]){re.escape(phrase)}(?![ऀ-ॿ])", eng, text)
    return text


_DEVANAGARI_RUN = re.compile(r"[ऀ-ॿ][ऀ-ॿ\s]*[ऀ-ॿ]|[ऀ-ॿ]")


def _transliterate_leftover(text: str) -> str:
    """Best-effort ASCII transliteration (via unidecode) for whatever
    Devanagari survives the dictionary passes below -- per explicit user
    decision, this covers building/street names buried in the long
    property-description blocks (hand-translated for PARTY_NAMES instead,
    see that dict's own docstring), NOT hand-verified, so flagged with a
    trailing [auto] marker wherever it fires."""
    def repl(m):
        chunk = m.group(0)
        try:
            from unidecode import unidecode
            rendered = unidecode(chunk).strip()
        except Exception:
            rendered = chunk
        return f"{rendered}[auto]" if rendered else chunk
    return _DEVANAGARI_RUN.sub(repl, text)


def translate_marathi(text: str) -> str:
    """Applies DEED_TYPES + PARTY_NAMES (hand-translated) + PHRASES +
    CONNECTORS substitution (longest match first within each dict), then
    runs whatever Devanagari still remains through a best-effort automated
    transliteration (_transliterate_leftover) rather than shipping it
    untranslated -- per explicit user decision, party names get careful
    hand translation while incidental building/street names elsewhere get
    an automated (unverified, marked [auto]) rendering instead."""
    if not text:
        return text
    for mr, en in sorted(DEED_TYPES.items(), key=lambda kv: -len(kv[0])):
        text = text.replace(mr, en)
    for mr, en in sorted(PARTY_NAMES.items(), key=lambda kv: -len(kv[0])):
        text = text.replace(mr, en)
    text = _translate_segment(text)
    text = _transliterate_leftover(text)
    text = re.sub(r"\s{2,}", " ", text).strip(" ,")
    return text


_NORMALIZED_PARTY_NAMES = {
    re.sub(r"\s+", "", mr): en for mr, en in PARTY_NAMES.items()
}


def _translate_unknown_name(piece: str) -> str:
    """For a name not already in the hand-built PARTY_NAMES dict -- calls
    captcha_solver.translate_indian_name() (Claude, via the same
    budget-capped infrastructure solve_*_captcha uses) so a NEW dataset's
    names no longer need manual reading/translation one by one. Falls back
    to translate_marathi()'s dictionary + auto-transliteration if the API
    call fails for any reason (no key, budget exhausted, network error) --
    never fatal, same discipline as every other Claude call in this
    codebase."""
    try:
        import captcha_solver
        return captcha_solver.translate_indian_name(
            piece, context="a party in a Maharashtra property registration document"
        )
    except Exception:
        return translate_marathi(piece)


def translate_name_field(raw: str) -> str:
    """For the main results table's own Seller Name/Purchaser Name cells --
    CONFIRMED LIVE 2026-10-15: these carry the SAME names as Index II's
    executant_party/claimant_party, but with every space stripped and
    wrapped in {}/\"\" (e.g. '{राजेंद्रप्रभाकरजळगावकर}' for the same person
    PARTY_NAMES has as 'राजेंद्र प्रभाकर जळगावकर'). Strips that wrapping,
    matches against PARTY_NAMES space-insensitively, and falls back to
    _translate_unknown_name() (Claude-based, see that function's own
    docstring for why a plain dictionary/unidecode fallback alone isn't
    good enough for names specifically) for anything not found -- e.g. a
    second/third party in a multi-party deed, or any name from a dataset
    this session never hand-translated at all."""
    if not raw:
        return raw
    cleaned = raw.strip().strip("{}").strip('"').strip()
    # A multi-party cell can hold several {name} chunks back to back, or
    # comma-separated -- split conservatively on the delimiters actually
    # seen (}, { and ,) and translate each piece independently.
    pieces = re.split(r'["{},]+', cleaned)
    out = []
    for piece in pieces:
        piece = piece.strip().rstrip("-").strip()
        if not piece:
            continue
        normalized = re.sub(r"\s+", "", piece)
        if normalized in _NORMALIZED_PARTY_NAMES:
            out.append(_NORMALIZED_PARTY_NAMES[normalized])
        else:
            out.append(_translate_unknown_name(piece))
    return "; ".join(out) if out else _translate_unknown_name(cleaned)


def remaining_devanagari(text: str) -> list[str]:
    """Lists the distinct untranslated Devanagari runs left in `text` --
    diagnostic only; translate_marathi() itself no longer leaves any,
    since _transliterate_leftover now auto-renders whatever the
    dictionaries miss."""
    if not text:
        return []
    return sorted(set(m.group(0).strip() for m in _DEVANAGARI_RUN.finditer(text)))
