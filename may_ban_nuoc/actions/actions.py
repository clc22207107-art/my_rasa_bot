"""
actions.py - Máy bán nước tự động (Rasa Custom Actions)
Phiên bản nâng cấp với giỏ hàng nhiều sản phẩm, flow thanh toán cải tiến
"""

from rasa_sdk import Action, Tracker
from rasa_sdk.executor import CollectingDispatcher
from rasa_sdk.events import SlotSet, AllSlotsReset, ConversationPaused
from typing import Any, Dict, List, Text, Optional
import re
import unicodedata
import json

# ============================================================
# INLINE DATABASE (30 sản phẩm)
# ============================================================

DRINKS_DB = {
    "coca": {
        "name": "Coca-Cola",
        "aliases": ["coca", "coca cola", "coke", "coca-cola", "cocacola"],
        "brand": "Coca-Cola Company", "volumes": ["330ml", "500ml", "1.5L"],
        "default_volume": "330ml", "price": {"330ml": 12000, "500ml": 15000, "1.5L": 28000},
        "ingredients": "Nước, đường, CO2, màu caramel, acid phosphoric, hương liệu tự nhiên, caffeine",
        "flavor": "Vị ngọt đặc trưng, có ga, hương caramel nhẹ",
        "features": "Nước ngọt có ga kinh điển, giải khát tốt",
        "category": "Nước ngọt có ga", "is_new": False, "popularity": 9.5,
        "sales": 1500, "stock": 120, "expiry_months": 9,
        "has_sugar": True, "has_caffeine": True, "image": "🥤",
    },
    "pepsi": {
        "name": "Pepsi",
        "aliases": ["pepsi"],
        "brand": "PepsiCo", "volumes": ["330ml", "500ml", "1.5L"],
        "default_volume": "330ml", "price": {"330ml": 11000, "500ml": 14000, "1.5L": 26000},
        "ingredients": "Nước, đường, CO2, acid phosphoric, màu caramel, hương liệu, caffeine",
        "flavor": "Vị ngọt nhẹ hơn Coca, có ga, hương vanilla thoảng nhẹ",
        "features": "Nước ngọt có ga phổ biến toàn cầu",
        "category": "Nước ngọt có ga", "is_new": False, "popularity": 9.0,
        "sales": 1300, "stock": 100, "expiry_months": 9,
        "has_sugar": True, "has_caffeine": True, "image": "🥤",
    },
    "sting": {
        "name": "Sting",
        "aliases": ["sting", "nước tăng lực sting", "nuoc tang luc sting"],
        "brand": "PepsiCo", "volumes": ["330ml"], "default_volume": "330ml",
        "price": {"330ml": 10000},
        "ingredients": "Nước, đường, acid citric, taurine, caffeine, vitamin B3, B6, B12, hương dâu",
        "flavor": "Vị ngọt, hương dâu đặc trưng",
        "features": "Nước tăng lực phổ biến, giá rẻ, bổ sung năng lượng nhanh",
        "category": "Nước tăng lực", "is_new": False, "popularity": 8.8,
        "sales": 200, "stock": 150, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": True, "image": "⚡",
    },
    "redbull": {
        "name": "Red Bull",
        "aliases": ["redbull", "red bull", "bò húc", "bo huc", "red bull energy", "redbull energy"],
        "brand": "Red Bull GmbH", "volumes": ["250ml"], "default_volume": "250ml",
        "price": {"250ml": 18000},
        "ingredients": "Nước, đường, acid citric, taurine (1000mg), caffeine (80mg), niacinamide, vitamin B6, B12",
        "flavor": "Vị ngọt thanh, hơi chua nhẹ, có ga",
        "features": "Nước tăng lực cao cấp nhập khẩu, tăng sự tập trung và thể lực",
        "category": "Nước tăng lực", "is_new": False, "popularity": 9.2,
        "sales": 900, "stock": 80, "expiry_months": 18,
        "has_sugar": True, "has_caffeine": True, "image": "🐂",
    },
    "sprite": {
        "name": "Sprite",
        "aliases": ["sprite"],
        "brand": "Coca-Cola Company", "volumes": ["330ml", "500ml"],
        "default_volume": "330ml", "price": {"330ml": 11000, "500ml": 14000},
        "ingredients": "Nước, đường, CO2, acid citric, hương chanh tự nhiên",
        "flavor": "Vị chua ngọt, hương chanh tươi mát, có ga",
        "features": "Nước ngọt có ga không màu, giải khát mùa hè",
        "category": "Nước ngọt có ga", "is_new": False, "popularity": 8.5,
        "sales": 1000, "stock": 90, "expiry_months": 9,
        "has_sugar": True, "has_caffeine": False, "image": "🍋",
    },
    "7up": {
        "name": "7UP",
        "aliases": ["7up", "7 up", "seven up", "7up chanh"],
        "brand": "PepsiCo", "volumes": ["330ml", "500ml"],
        "default_volume": "330ml", "price": {"330ml": 10000, "500ml": 13000},
        "ingredients": "Nước, đường, CO2, acid citric, hương chanh & chanh xanh",
        "flavor": "Vị chua ngọt nhẹ, hương chanh xanh, có ga",
        "features": "Nước ngọt có ga trong suốt, thanh mát",
        "category": "Nước ngọt có ga", "is_new": False, "popularity": 8.0,
        "sales": 800, "stock": 70, "expiry_months": 9,
        "has_sugar": True, "has_caffeine": False, "image": "🍋",
    },
    "fanta": {
        "name": "Fanta",
        "aliases": ["fanta", "fanta cam", "fanta nho"],
        "brand": "Coca-Cola Company", "volumes": ["330ml", "500ml"],
        "default_volume": "330ml", "price": {"330ml": 11000, "500ml": 14000},
        "ingredients": "Nước, đường, CO2, acid citric, hương cam/nho tự nhiên, màu thực phẩm",
        "flavor": "Vị ngọt đậm, hương trái cây (cam hoặc nho), có ga",
        "features": "Nước ngọt có ga hương trái cây, nhiều vị đa dạng",
        "category": "Nước ngọt có ga", "is_new": False, "popularity": 8.2,
        "sales": 850, "stock": 85, "expiry_months": 9,
        "has_sugar": True, "has_caffeine": False, "image": "🍊",
    },
    "mirinda": {
        "name": "Mirinda",
        "aliases": ["mirinda", "mirinda cam"],
        "brand": "PepsiCo", "volumes": ["330ml"], "default_volume": "330ml",
        "price": {"330ml": 10000},
        "ingredients": "Nước, đường, CO2, acid citric, hương cam, màu thực phẩm",
        "flavor": "Vị ngọt đậm đà, hương cam nổi bật, có ga",
        "features": "Nước ngọt có ga hương cam đặc trưng",
        "category": "Nước ngọt có ga", "is_new": False, "popularity": 7.5,
        "sales": 600, "stock": 60, "expiry_months": 9,
        "has_sugar": True, "has_caffeine": False, "image": "🍊",
    },
    "aquafina": {
        "name": "Aquafina",
        "aliases": ["aquafina", "nước suối aquafina", "nuoc suoi aquafina"],
        "brand": "PepsiCo", "volumes": ["500ml", "1.5L"], "default_volume": "500ml",
        "price": {"500ml": 7000, "1.5L": 12000},
        "ingredients": "Nước tinh khiết (qua lọc RO 7 bước)",
        "flavor": "Vị thanh khiết, không mùi không vị",
        "features": "Nước tinh khiết đóng chai, lọc 7 bước RO",
        "category": "Nước suối / tinh khiết", "is_new": False, "popularity": 8.8,
        "sales": 1100, "stock": 200, "expiry_months": 24,
        "has_sugar": False, "has_caffeine": False, "image": "💧",
    },
    "lavie": {
        "name": "La Vie",
        "aliases": ["lavie", "la vie", "nước suối lavie", "nuoc suoi lavie"],
        "brand": "Nestlé", "volumes": ["500ml", "1.5L"], "default_volume": "500ml",
        "price": {"500ml": 8000, "1.5L": 13000},
        "ingredients": "Nước khoáng thiên nhiên, khoáng chất tự nhiên (Ca, Mg, Na...)",
        "flavor": "Vị thanh nhẹ, chứa khoáng chất tự nhiên",
        "features": "Nước khoáng thiên nhiên, bổ sung khoáng chất cho cơ thể",
        "category": "Nước khoáng", "is_new": False, "popularity": 8.5,
        "sales": 950, "stock": 180, "expiry_months": 24,
        "has_sugar": False, "has_caffeine": False, "image": "💧",
    },
    "revive": {
        "name": "Revive",
        "aliases": ["revive"],
        "brand": "Coca-Cola Company", "volumes": ["500ml"], "default_volume": "500ml",
        "price": {"500ml": 10000},
        "ingredients": "Nước, đường, muối, kali citrate, natri citrate, kẽm gluconate, vitamin C, hương chanh muối",
        "flavor": "Vị mặn ngọt nhẹ, hương chanh muối đặc trưng",
        "features": "Nước điện giải bù khoáng, tốt sau vận động hoặc mất nước",
        "category": "Nước điện giải", "is_new": False, "popularity": 8.3,
        "sales": 700, "stock": 90, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": False, "image": "⚗️",
    },
    "c2": {
        "name": "C2",
        "aliases": ["c2", "trà xanh c2", "c2 chanh", "tra xanh c2", "c2 huong chanh"],
        "brand": "URC Việt Nam", "volumes": ["360ml", "455ml"], "default_volume": "360ml",
        "price": {"360ml": 9000, "455ml": 11000},
        "ingredients": "Nước, đường, chiết xuất trà xanh, acid citric, hương chanh, vitamin C",
        "flavor": "Vị ngọt nhẹ, hương trà xanh và chanh tươi mát",
        "features": "Trà xanh đóng chai phổ biến, chứa chất chống oxy hóa",
        "category": "Trà đóng chai", "is_new": False, "popularity": 8.7,
        "sales": 1050, "stock": 110, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": True, "image": "🍵",
    },
    "tra_xanh_khong_do": {
        "name": "Trà Xanh Không Độ",
        "aliases": ["trà xanh không độ", "không độ", "tra xanh khong do",
                    "khong do", "nuoc tra xanh khong do"],
        "brand": "Tân Hiệp Phát", "volumes": ["350ml", "500ml"], "default_volume": "350ml",
        "price": {"350ml": 9000, "500ml": 12000},
        "ingredients": "Nước, chiết xuất trà xanh, đường, acid citric, hương jasmine",
        "flavor": "Vị trà nhẹ, hương hoa nhài thoảng, ít ngọt",
        "features": "Trà xanh thuần Việt, ít calo, giải khát tự nhiên",
        "category": "Trà đóng chai", "is_new": False, "popularity": 8.6,
        "sales": 980, "stock": 100, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": True, "image": "🍵",
    },
    "olong_tea": {
        "name": "Olong Tea+",
        "aliases": ["olong tea", "olong", "trà ô long", "olong tea plus",
                    "tra o long", "o long", "olong tea+"],
        "brand": "Tân Hiệp Phát", "volumes": ["350ml"], "default_volume": "350ml",
        "price": {"350ml": 9000},
        "ingredients": "Nước, chiết xuất trà ô long, đường thấp, hương trà tự nhiên",
        "flavor": "Vị trà đậm, hương ô long đặc trưng, ít ngọt",
        "features": "Trà ô long ít đường, giúp giảm béo và tốt cho tiêu hóa",
        "category": "Trà đóng chai", "is_new": False, "popularity": 7.8,
        "sales": 650, "stock": 75, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": True, "image": "🍵",
    },
    "dr_thanh": {
        "name": "Dr Thanh",
        "aliases": ["dr thanh", "dr. thanh", "nước thảo mộc dr thanh",
                    "dr.thanh", "nuoc thao moc dr thanh", "drthanh"],
        "brand": "Tân Hiệp Phát", "volumes": ["350ml"], "default_volume": "350ml",
        "price": {"350ml": 10000},
        "ingredients": "Nước, chiết xuất 9 loại thảo mộc (la hán quả, kim ngân hoa, hoa cúc...), đường, acid citric",
        "flavor": "Vị ngọt thanh, hương thảo mộc nhẹ, hơi đắng nhẹ đặc trưng",
        "features": "Nước thảo mộc thanh nhiệt, giải độc, tốt cho sức khỏe",
        "category": "Nước thảo mộc", "is_new": False, "popularity": 8.0,
        "sales": 720, "stock": 80, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": False, "image": "🌿",
    },
    "monster": {
        "name": "Monster Energy",
        "aliases": ["monster", "monster energy"],
        "brand": "Monster Beverage Corporation", "volumes": ["355ml", "500ml"], "default_volume": "355ml",
        "price": {"355ml": 25000, "500ml": 35000},
        "ingredients": "Nước, đường, CO2, taurine, ginseng extract, L-carnitine, caffeine (160mg/500ml), vitamin B",
        "flavor": "Vị ngọt mạnh, có ga, hương trái cây hỗn hợp",
        "features": "Nước tăng lực cao cấp nhập khẩu, caffeine cao, phù hợp gymer và gamer",
        "category": "Nước tăng lực", "is_new": False, "popularity": 8.9,
        "sales": 560, "stock": 60, "expiry_months": 24,
        "has_sugar": True, "has_caffeine": True, "image": "👾",
    },
    "number1": {
        "name": "Number 1",
        "aliases": ["number 1", "số 1", "nước tăng lực số 1",
                    "number1", "so 1", "nuoc tang luc so 1", "num 1"],
        "brand": "Tân Hiệp Phát", "volumes": ["330ml"], "default_volume": "330ml",
        "price": {"330ml": 10000},
        "ingredients": "Nước, đường, taurine, inositol, caffeine, vitamin B3, B6, B12",
        "flavor": "Vị ngọt, hương sâm nhẹ",
        "features": "Nước tăng lực Việt, giá tốt, phù hợp mọi đối tượng",
        "category": "Nước tăng lực", "is_new": False, "popularity": 7.5,
        "sales": 700, "stock": 100, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": True, "image": "⚡",
    },
    "warrior": {
        "name": "Warrior",
        "aliases": ["warrior"],
        "brand": "Tân Hiệp Phát", "volumes": ["330ml"], "default_volume": "330ml",
        "price": {"330ml": 9000},
        "ingredients": "Nước, đường, taurine (800mg), caffeine, vitamin B6, vitamin B12, niacin, acid citric",
        "flavor": "Vị ngọt, nhẹ ga, hương trái cây",
        "features": "Nước tăng lực giá rẻ, phù hợp học sinh sinh viên",
        "category": "Nước tăng lực", "is_new": False, "popularity": 7.0,
        "sales": 580, "stock": 90, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": True, "image": "⚡",
    },
    "yakult": {
        "name": "Yakult",
        "aliases": ["yakult"],
        "brand": "Yakult Honsha", "volumes": ["65ml"], "default_volume": "65ml",
        "price": {"65ml": 8000},
        "ingredients": "Nước, sữa gầy, đường, men vi sinh Lactobacillus casei Shirota (6.5 tỷ vi khuẩn/chai)",
        "flavor": "Vị chua ngọt đặc trưng, thơm sữa",
        "features": "Sữa chua uống men vi sinh, tốt cho tiêu hóa và hệ miễn dịch",
        "category": "Sữa chua uống", "is_new": False, "popularity": 8.4,
        "sales": 800, "stock": 120, "expiry_months": 1,
        "has_sugar": True, "has_caffeine": False, "image": "🍶",
    },
    "vinamilk": {
        "name": "Vinamilk Sô-cô-la",
        "aliases": ["vinamilk", "vinamilk socola", "sữa vinamilk", "sua vinamilk"],
        "brand": "Vinamilk", "volumes": ["180ml", "250ml"], "default_volume": "180ml",
        "price": {"180ml": 8000, "250ml": 12000},
        "ingredients": "Sữa tươi, đường, bột cacao, hương vani",
        "flavor": "Vị ngọt, béo thơm, hương socola đậm đà",
        "features": "Sữa tươi tiệt trùng hương socola, giàu canxi và protein",
        "category": "Sữa", "is_new": False, "popularity": 8.0,
        "sales": 650, "stock": 85, "expiry_months": 6,
        "has_sugar": True, "has_caffeine": False, "image": "🍫",
    },
    "th_true_milk": {
        "name": "TH True Milk",
        "aliases": ["th true milk", "th milk", "sữa th", "sua th", "th truemilk"],
        "brand": "TH Group", "volumes": ["180ml", "500ml", "1L"], "default_volume": "180ml",
        "price": {"180ml": 9000, "500ml": 18000, "1L": 32000},
        "ingredients": "Sữa tươi nguyên chất 100%, vitamin A, D, B2, canxi",
        "flavor": "Vị ngọt thanh, béo nhẹ, thơm mùi sữa tự nhiên",
        "features": "Sữa tươi nguyên chất 100%, không chất bảo quản, từ trang trại sạch",
        "category": "Sữa", "is_new": False, "popularity": 8.7,
        "sales": 750, "stock": 80, "expiry_months": 1,
        "has_sugar": True, "has_caffeine": False, "image": "🥛",
    },
    "dutch_lady": {
        "name": "Dutch Lady",
        "aliases": ["dutch lady", "cô gái hà lan", "sữa dutch lady",
                    "co gai ha lan", "sua dutch lady", "dutchlady"],
        "brand": "FrieslandCampina", "volumes": ["180ml", "1L"], "default_volume": "180ml",
        "price": {"180ml": 8500, "1L": 30000},
        "ingredients": "Sữa tươi, đường, vitamin (A, D, B1, B2, B6, C), canxi, sắt",
        "flavor": "Vị ngọt vừa, thơm, béo nhẹ",
        "features": "Sữa tiệt trùng giàu dinh dưỡng, bổ sung vitamin và khoáng chất",
        "category": "Sữa", "is_new": False, "popularity": 8.1,
        "sales": 600, "stock": 70, "expiry_months": 6,
        "has_sugar": True, "has_caffeine": False, "image": "🥛",
    },
    "nescafe": {
        "name": "Nescafé RTD",
        "aliases": ["nescafe", "nescafé", "cafe nescafe", "ca phe nescafe"],
        "brand": "Nestlé", "volumes": ["180ml"], "default_volume": "180ml",
        "price": {"180ml": 15000},
        "ingredients": "Nước, đường, cà phê hòa tan (2%), sữa, hương cà phê tự nhiên",
        "flavor": "Vị đắng nhẹ, thơm cà phê, ngọt vừa",
        "features": "Cà phê uống liền tiện lợi, tỉnh táo nhanh",
        "slogan": "Open up",
        "category": "Cà phê đóng lon", "is_new": False, "popularity": 7.8,
        "sales": 480, "stock": 60, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": True, "image": "☕",
    },
    "birdy": {
        "name": "Café Birdy",
        "aliases": ["birdy", "cafe birdy", "ca phe birdy"],
        "brand": "Ajinomoto", "volumes": ["170ml"], "default_volume": "170ml",
        "price": {"170ml": 12000},
        "ingredients": "Nước, đường, cà phê Robusta, sữa đặc, hương cà phê",
        "flavor": "Vị đắng đậm đà, ngọt sữa, hương cà phê Robusta mạnh",
        "features": "Cà phê lon Thái Lan nổi tiếng, vị đậm đà đặc trưng",
        "category": "Cà phê đóng lon", "is_new": False, "popularity": 7.5,
        "sales": 400, "stock": 50, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": True, "image": "☕",
    },
    "lipton": {
        "name": "Lipton Trà Đào",
        "aliases": ["lipton", "trà lipton", "lipton đào", "tra lipton", "lipton dao"],
        "brand": "Unilever", "volumes": ["330ml", "455ml"], "default_volume": "330ml",
        "price": {"330ml": 10000, "455ml": 13000},
        "ingredients": "Nước, đường, chiết xuất trà, acid citric, hương đào tự nhiên, vitamin C",
        "flavor": "Vị ngọt nhẹ, hương đào thơm tươi mát",
        "features": "Trà đào đóng chai, thanh mát, ít calo hơn nước ngọt",
        "category": "Trà đóng chai", "is_new": False, "popularity": 7.9,
        "sales": 680, "stock": 80, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": True, "image": "🍑",
    },
    "nestea": {
        "name": "Nestea Trà Đào",
        "aliases": ["nestea", "nestea đào", "nestea dao"],
        "brand": "Nestlé", "volumes": ["330ml"], "default_volume": "330ml",
        "price": {"330ml": 10000},
        "ingredients": "Nước, đường, chiết xuất trà, hương đào, acid citric, vitamin C",
        "flavor": "Vị chua ngọt, hương đào đậm hơn Lipton",
        "features": "Trà đào đóng lon, giải khát tốt",
        "category": "Trà đóng chai", "is_new": False, "popularity": 7.6,
        "sales": 550, "stock": 65, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": True, "image": "🍑",
    },
    "cocoxim": {
        "name": "Cocoxim Nước Dừa",
        "aliases": ["cocoxim", "nước dừa cocoxim", "nước dừa",
                    "nuoc dua cocoxim", "nuoc dua"],
        "brand": "Cocoxim", "volumes": ["330ml", "1L"], "default_volume": "330ml",
        "price": {"330ml": 15000, "1L": 38000},
        "ingredients": "Nước dừa tươi nguyên chất (100%), không thêm đường, không chất bảo quản",
        "flavor": "Vị ngọt nhẹ tự nhiên, thanh mát đặc trưng của dừa tươi",
        "features": "Nước dừa nguyên chất, giàu điện giải tự nhiên, không đường thêm vào",
        "category": "Nước trái cây / dừa", "is_new": False, "popularity": 8.3,
        "sales": 620, "stock": 70, "expiry_months": 12,
        "has_sugar": False, "has_caffeine": False, "image": "🥥",
    },
    "twister": {
        "name": "Twister Nước Cam",
        "aliases": ["twister", "nước cam twister", "twister cam", "nuoc cam twister"],
        "brand": "Coca-Cola Company", "volumes": ["455ml"], "default_volume": "455ml",
        "price": {"455ml": 12000},
        "ingredients": "Nước cam ép (15%), nước, đường, acid citric, vitamin C, hương cam tự nhiên",
        "flavor": "Vị chua ngọt, hương cam tươi",
        "features": "Nước cam ép phổ biến, bổ sung vitamin C",
        "category": "Nước trái cây", "is_new": False, "popularity": 7.7,
        "sales": 500, "stock": 60, "expiry_months": 9,
        "has_sugar": True, "has_caffeine": False, "image": "🍊",
    },
    "aloe_vera": {
        "name": "Aloe Vera Nha Đam",
        "aliases": ["aloe vera", "nha đam", "nước nha đam", "aloe",
                    "nha dam", "nuoc nha dam", "aloe vera nha dam"],
        "brand": "Woongjin", "volumes": ["500ml"], "default_volume": "500ml",
        "price": {"500ml": 18000},
        "ingredients": "Nước, đường, thịt nha đam (8%), acid citric, vitamin C, hương nha đam",
        "flavor": "Vị ngọt nhẹ, thanh mát, có thịt nha đam giòn giòn",
        "features": "Nước nha đam Hàn Quốc, tốt cho da và tiêu hóa",
        "category": "Nước trái cây / thảo mộc", "is_new": True, "popularity": 8.1,
        "sales": 430, "stock": 55, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": False, "image": "🌵",
    },
    "wake_up_247": {
        "name": "Wake Up 247",
        "aliases": ["wake up", "wake up 247", "cà phê wake up",
                    "ca phe wake up", "wakeup247", "wake up247"],
        "brand": "Tân Hiệp Phát", "volumes": ["240ml"], "default_volume": "240ml",
        "price": {"240ml": 13000},
        "ingredients": "Nước, cà phê rang xay (Robusta & Arabica), đường, sữa, hương cà phê",
        "flavor": "Vị đắng đậm, thơm cà phê rang, ngọt vừa",
        "features": "Cà phê lon Việt, vị đậm đà, giá tốt",
        "category": "Cà phê đóng lon", "is_new": False, "popularity": 7.6,
        "sales": 450, "stock": 55, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": True, "image": "☕",
    },
    "tra_gao_rut": {
        "name": "Trà Gạo Lứt Rang",
        "aliases": ["trà gạo lứt", "gạo lứt", "trà gạo rang",
                    "tra gao lut", "gao lut", "tra gao rang"],
        "brand": "Fami", "volumes": ["350ml"], "default_volume": "350ml",
        "price": {"350ml": 10000},
        "ingredients": "Nước, gạo lứt rang, đường thốt nốt, muối tinh, hương gạo tự nhiên",
        "flavor": "Vị bùi thơm của gạo rang, ngọt nhẹ, hương quê đặc trưng",
        "features": "Trà gạo lứt thuần Việt, tốt cho người ăn kiêng và người tiểu đường",
        "category": "Trà thảo mộc", "is_new": True, "popularity": 7.2,
        "sales": 280, "stock": 40, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": False, "image": "🌾",
    },
    "vita_milk": {
        "name": "Vita Milk Sữa Đậu Nành",
        "aliases": ["vita milk", "vitamilk", "sữa đậu nành",
                    "sua dau nanh", "vitamilk sua dau nanh"],
        "brand": "Vita Food", "volumes": ["200ml"], "default_volume": "200ml",
        "price": {"200ml": 9000},
        "ingredients": "Nước, đậu nành (20%), đường, muối, vitamin D, canxi",
        "flavor": "Vị ngọt béo đặc trưng của đậu nành, thơm nhẹ",
        "features": "Sữa đậu nành Thái Lan nổi tiếng, giàu protein thực vật",
        "category": "Sữa thực vật", "is_new": False, "popularity": 7.9,
        "sales": 520, "stock": 65, "expiry_months": 12,
        "has_sugar": True, "has_caffeine": False, "image": "🫘",
    },
}

# ============================================================
# QR CODE PLACEHOLDER
# ============================================================
BANK_QR_INFO = {
    "bank": "Vietcombank",
    "account": "1234567890",
    "name": "MÁYBANNUTCUAHANGTUDONG",
    "qr_text": "[QR CODE CHUYỂN KHOẢN]\nNgân hàng: Vietcombank\nSố TK: 1234 5678 90\nChủ TK: MÁY BÁN NƯỚC TỰ ĐỘNG\nNội dung: <Số đơn hàng>",
}

# ============================================================
# HELPER FUNCTIONS
# ============================================================

def normalize_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace('đ', 'd').replace('Đ', 'd')
    nfkd = unicodedata.normalize('NFKD', text)
    ascii_str = ''.join(c for c in nfkd if not unicodedata.combining(c))
    ascii_str = re.sub(r'[^\w\s]', ' ', ascii_str)
    return re.sub(r'\s+', ' ', ascii_str).lower().strip()


def find_drink(name: str):
    if not name:
        return None, None
    name_norm = normalize_text(name)
    name_no_space = name_norm.replace(' ', '')

    for key, drink in DRINKS_DB.items():
        key_norm = normalize_text(key)
        if name_norm == key_norm:
            return key, drink
        for alias in drink["aliases"]:
            alias_norm = normalize_text(alias)
            alias_no_space = alias_norm.replace(' ', '')
            if name_norm == alias_norm:
                return key, drink
            if name_no_space == alias_no_space:
                return key, drink
            if len(alias_norm) >= 4 and alias_norm in name_norm:
                return key, drink
            if len(name_norm) >= 4 and name_norm in alias_norm:
                return key, drink
    return None, None


def find_drink_from_message(message: str):
    if not message:
        return None, None
    msg_norm = normalize_text(message)
    msg_no_space = msg_norm.replace(' ', '')

    candidates = []
    for key, drink in DRINKS_DB.items():
        for alias in drink["aliases"]:
            alias_norm = normalize_text(alias)
            alias_no_space = alias_norm.replace(' ', '')
            if len(alias_norm) < 2:
                continue
            if alias_norm in msg_norm or alias_no_space in msg_no_space:
                candidates.append((len(alias_norm), key, drink))

    if not candidates:
        return None, None
    candidates.sort(key=lambda x: x[0], reverse=True)
    _, best_key, best_drink = candidates[0]
    return best_key, best_drink


def find_all_drinks_from_message(message: str):
    """
    Tìm TẤT CẢ các sản phẩm được nhắc đến trong 1 câu, kèm số lượng.
    Ví dụ: "cho 2 hộp sữa cô gái hà lan và 1 lon coca"
    Trả về list of (key, drink_data, quantity)
    """
    if not message:
        return []
    msg_norm = normalize_text(message)
    msg_no_space = msg_norm.replace(' ', '')

    # Bước 1: Tìm tất cả (alias, key, drink) khớp trong message
    # Ưu tiên alias dài nhất để tránh match nhầm
    candidates = []
    for key, drink in DRINKS_DB.items():
        for alias in drink["aliases"]:
            alias_norm = normalize_text(alias)
            alias_no_space = alias_norm.replace(' ', '')
            if len(alias_norm) < 2:
                continue
            if alias_norm in msg_norm:
                pos = msg_norm.find(alias_norm)
                candidates.append((len(alias_norm), pos, alias_norm, key, drink))
            elif alias_no_space in msg_no_space:
                pos = msg_no_space.find(alias_no_space)
                candidates.append((len(alias_norm), pos, alias_norm, key, drink))

    if not candidates:
        return []

    # Bước 2: Loại bỏ trùng lặp cùng key — giữ alias dài nhất
    best_per_key = {}
    for length, pos, alias_norm, key, drink in candidates:
        if key not in best_per_key or length > best_per_key[key][0]:
            best_per_key[key] = (length, pos, alias_norm, key, drink)

    # Bước 3: Sắp xếp theo vị trí xuất hiện trong câu
    sorted_matches = sorted(best_per_key.values(), key=lambda x: x[1])

    # Bước 4: Với mỗi sản phẩm, tìm số lượng trong đoạn text TRƯỚC vị trí xuất hiện
    word_nums = {
        "mot": 1, "hai": 2, "ba": 3, "bon": 4, "nam": 5,
        "sau": 6, "bay": 7, "tam": 8, "chin": 9, "muoi": 10,
        "một": 1, "bốn": 4, "năm": 5, "sáu": 6, "bảy": 7,
        "tám": 8, "chín": 9, "mười": 10,
    }

    results = []
    for length, pos, alias_norm, key, drink in sorted_matches:
        # Lấy đoạn text phía trước alias (tối đa 25 ký tự, sau dấu phân cách cuối)
        prefix = msg_norm[:pos]
        # Cắt từ dấu phân cách gần nhất (và, với, +, ,)
        for sep in [" va ", " voi ", " cung ", " them ", ","]:
            idx = prefix.rfind(sep)
            if idx >= 0:
                prefix = prefix[idx + len(sep):]
                break
        prefix = prefix.strip()

        qty = 1
        # Tìm số nguyên trước
        nums = re.findall(r'\d+', prefix)
        if nums:
            qty = int(nums[-1])
        else:
            for word, num in word_nums.items():
                word_norm = normalize_text(word)
                if word_norm in prefix:
                    qty = num
                    break

        results.append((key, drink, qty))

    return results


def resolve_drink(tracker):
    last_msg = tracker.latest_message.get("text", "")
    key, drink_data = find_drink_from_message(last_msg)
    if drink_data:
        return key, drink_data
    drink_slot = tracker.get_slot("drink")
    return find_drink(drink_slot)


def parse_quantity(qty_str: str) -> int:
    if not qty_str:
        return 1
    words = {
        "một": 1, "hai": 2, "ba": 3, "bốn": 4, "năm": 5,
        "sáu": 6, "bảy": 7, "tám": 8, "chín": 9, "mười": 10,
        "mot": 1, "bon": 4, "nam": 5, "sau": 6, "bay": 7,
        "tam": 8, "chin": 9, "muoi": 10,
        "one": 1, "two": 2, "three": 3,
    }
    qty_lower = qty_str.lower()
    for word, num in words.items():
        if word in qty_lower:
            return num
    numbers = re.findall(r'\d+', qty_str)
    return int(numbers[0]) if numbers else 1


def resolve_volume(drink_data: dict, size_slot: str) -> str:
    if not size_slot:
        return drink_data["default_volume"]
    size_lower = size_slot.lower()
    for vol in drink_data["volumes"]:
        if vol.lower() in size_lower or size_lower in vol.lower():
            return vol
    size_norm = normalize_text(size_slot)
    if any(w in size_norm for w in ["lon", "to", "big", "large", "xl"]):
        return drink_data["volumes"][-1]
    if any(w in size_norm for w in ["nho", "small", "s"]):
        return drink_data["volumes"][0]
    return drink_data["default_volume"]


def get_cart(tracker) -> list:
    """Lấy giỏ hàng từ slot cart (JSON string). Trả về list of dict."""
    cart_str = tracker.get_slot("cart")
    if not cart_str:
        return []
    try:
        return json.loads(cart_str)
    except Exception:
        return []


def cart_total(cart: list) -> int:
    return sum(item["subtotal"] for item in cart)


def format_cart(cart: list) -> str:
    """Hiển thị giỏ hàng dạng bảng."""
    if not cart:
        return "🛒 Giỏ hàng trống."
    lines = ["🛒 **GIỎ HÀNG HIỆN TẠI**\n" + "─" * 32]
    for i, item in enumerate(cart, 1):
        lines.append(
            f"{i}. {item['image']} {item['name']} ({item['volume']})\n"
            f"   x{item['qty']} × {item['unit_price']:,}đ = {item['subtotal']:,}đ"
        )
    lines.append("─" * 32)
    lines.append(f"💵 **Tổng: {cart_total(cart):,}đ**")
    return "\n".join(lines)


def detect_payment_method(text: str) -> str:
    """
    Nhận diện phương thức thanh toán từ câu nói của khách.
    Trả về: 'qr' | 'cash' | 'card' | 'pay' (yêu cầu thanh toán chung) | ''
    """
    norm = normalize_text(text)

    # Chuyển khoản / QR — kiểm tra trước vì "quet" có thể nhầm với "quẹt thẻ"
    qr_keywords = [
        "chuyen khoan", "banking", "ma qr", "bank transfer",
        "quet ma qr", "quet qr", "quet ma", "chuyen tien",
        "chuyen tien qua dien thoai", "chuyen tien dien thoai",
        "thanh toan chuyen khoan", "internet banking",
        "qr code", "qr di",
        # Ví điện tử (thường dùng QR)
        "momo", "zalopay", "vnpay", "zalo pay",
    ]
    # Quẹt thẻ — phải kiểm tra TRƯỚC "tien" để tránh nhầm
    card_keywords = [
        "quet the", "ca the", "thanh toan bang the", "thanh toan the",
        "credit card", "debit card", "visa", "mastercard", "atm",
        "the ngan hang", "the tin dung", "the ghi no",
        "the di", "ca the nhe", "dua the", "quet the di",
    ]
    # Tiền mặt
    cash_keywords = [
        "tien mat", "cash", "tra tien mat", "tien giay",
        "tien le", "thanh toan tien mat", "dung tien",
        "cho tien vao", "bo tien vao",
    ]
    # Yêu cầu thanh toán chung (không chỉ định phương thức)
    pay_general_keywords = [
        "thanh toan", "tra tien", "mua di", "mua thoi",
        "tính tien", "tinh tien", "dat luon", "thanh toan di",
        "thanh toan cho toi", "toi muon thanh toan", "toi can thanh toan",
    ]

    # Ưu tiên QR trước (vì "quet" có thể xuất hiện trong cả QR và thẻ)
    for kw in qr_keywords:
        if kw in norm:
            return "qr"
    # Quẹt thẻ
    for kw in card_keywords:
        if kw in norm:
            return "card"
    # Tiền mặt
    for kw in cash_keywords:
        if kw in norm:
            return "cash"
    # Thanh toán chung (hỏi lại phương thức)
    for kw in pay_general_keywords:
        if kw in norm:
            return "pay"
    return ""


# ============================================================
# ACTIONS
# ============================================================

class ActionShowMenu(Action):
    def name(self) -> Text:
        return "action_show_menu"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        last_norm = normalize_text(tracker.latest_message.get("text", ""))

        if any(w in last_norm for w in ["san pham moi", "hang moi", "moi ra", "moi khong", "co gi moi", "moi nhat"]):
            new_products = [(k, v) for k, v in DRINKS_DB.items() if v["is_new"]]
            if not new_products:
                dispatcher.utter_message(text="Hiện tại chưa có sản phẩm mới nào. Gõ 'menu' để xem toàn bộ danh sách!")
                return []
            lines = ["🆕 **SẢN PHẨM MỚI**\n" + "─" * 35]
            for key, d in new_products:
                default_vol = d["default_volume"]
                price = d["price"][default_vol]
                lines.append(
                    f"\n{d['image']} **{d['name']}**\n"
                    f"   💰 Giá: {price:,}đ ({default_vol})\n"
                    f"   ✨ {d['features']}"
                )
            lines.append("\n💬 Bạn muốn biết thêm thông tin hoặc đặt sản phẩm nào không?")
            dispatcher.utter_message(text="\n".join(lines))
            return []

        categories = {}
        for key, drink in DRINKS_DB.items():
            cat = drink["category"]
            if cat not in categories:
                categories[cat] = []
            default_vol = drink["default_volume"]
            price = drink["price"][default_vol]
            new_badge = " 🆕" if drink["is_new"] else ""
            categories[cat].append(f"  {drink['image']} {drink['name']}{new_badge} ({default_vol}) - {price:,}đ")
        lines = ["📋 MENU ĐỒ UỐNG\n" + "─" * 35]
        for cat, items in categories.items():
            lines.append(f"\n🏷️ {cat}:")
            lines.extend(items)
        lines.append("\n💬 Hỏi tôi về bất kỳ sản phẩm nào để biết thêm chi tiết!")
        dispatcher.utter_message(text="\n".join(lines))
        return []


class ActionGetDrinkInfo(Action):
    def name(self) -> Text:
        return "action_get_drink_info"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        key, drink_data = resolve_drink(tracker)
        if not drink_data:
            dispatcher.utter_message(
                text="❌ Xin lỗi, tôi không tìm thấy sản phẩm này trong menu. Gõ 'menu' để xem danh sách nhé!"
            )
            return [SlotSet("drink", None)]
        if drink_data["stock"] == 0:
            dispatcher.utter_message(
                text=f"😔 Rất tiếc, **{drink_data['name']}** hiện đã hết hàng. Bạn muốn chọn sản phẩm khác không?"
            )
            return [SlotSet("drink", None)]
        return [SlotSet("drink", key)]


class ActionShowPrice(Action):
    def name(self) -> Text:
        return "action_show_price"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        _, drink_data = resolve_drink(tracker)
        if not drink_data:
            dispatcher.utter_message(text="Bạn muốn hỏi giá sản phẩm nào? (Gõ 'menu' để xem danh sách)")
            return []
        price_lines = [f"  • {vol}: {price:,}đ" for vol, price in drink_data["price"].items()]
        msg = f"{drink_data['image']} **{drink_data['name']}**\n💰 Giá:\n" + "\n".join(price_lines)
        dispatcher.utter_message(text=msg)
        return []


class ActionShowIngredients(Action):
    def name(self) -> Text:
        return "action_show_ingredients"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        _, drink_data = resolve_drink(tracker)
        if not drink_data:
            dispatcher.utter_message(text="Bạn muốn hỏi thành phần sản phẩm nào?")
            return []

        last_norm = normalize_text(tracker.latest_message.get("text", ""))

        if any(w in last_norm for w in ["caffeine", "cafein"]):
            if drink_data["has_caffeine"]:
                msg = f"{drink_data['image']} **{drink_data['name']}**\n☕ Có chứa caffeine."
            else:
                msg = f"{drink_data['image']} **{drink_data['name']}**\n✅ Không có caffeine."
            dispatcher.utter_message(text=msg)
            return []

        if any(w in last_norm for w in ["co duong", "duong khong", "co ngot", "it duong", "nhieu duong"]):
            if drink_data["has_sugar"]:
                msg = f"{drink_data['image']} **{drink_data['name']}**\n🍬 Có đường."
            else:
                msg = f"{drink_data['image']} **{drink_data['name']}**\n✅ Không đường / ít đường tự nhiên."
            dispatcher.utter_message(text=msg)
            return []

        if any(w in last_norm for w in ["co ga", "co gas", "nuoc co ga", "gas khong", "ga khong"]):
            has_gas = drink_data.get("category") in ["Nước ngọt có ga", "Nước tăng lực"]
            if has_gas:
                msg = f"{drink_data['image']} **{drink_data['name']}**\n🫧 Có ga."
            else:
                msg = f"{drink_data['image']} **{drink_data['name']}**\n✅ Không có ga."
            dispatcher.utter_message(text=msg)
            return []

        if any(w in last_norm for w in ["men vi sinh", "probiotic", "loi khuan"]):
            has_probiotic = any(
                w in drink_data["ingredients"].lower()
                for w in ["lactobacillus", "men vi sinh", "probiotic"]
            )
            if has_probiotic:
                msg = f"{drink_data['image']} **{drink_data['name']}**\n🦠 Có chứa men vi sinh."
            else:
                msg = f"{drink_data['image']} **{drink_data['name']}**\n❌ Không có men vi sinh."
            dispatcher.utter_message(text=msg)
            return []

        msg = (
            f"{drink_data['image']} **{drink_data['name']}**\n"
            f"🧪 Thành phần: {drink_data['ingredients']}"
        )
        dispatcher.utter_message(text=msg)
        return []


class ActionShowProductInfo(Action):
    def name(self) -> Text:
        return "action_show_product_info"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        _, drink_data = resolve_drink(tracker)
        if not drink_data:
            dispatcher.utter_message(text="Bạn muốn xem thông tin sản phẩm nào? (Gõ 'menu' để xem)")
            return []

        last_norm = normalize_text(tracker.latest_message.get("text", ""))
        msg_stripped = last_norm.strip()
        starts_with_con = msg_stripped.startswith("con ")
        ends_with_check = any(msg_stripped.endswith(w) for w in ["khong", "chua", "khong a"])

        stock_keywords = [
            "so luong", "con bao nhieu", "con lai", "ton kho", "stock",
            "het hang", "con hang", "bao nhieu lon", "bao nhieu chai",
            "con ban", "het chua", "con khong", "kiem tra hang",
        ]

        # ── Phát hiện TẤT CẢ các loại thông tin được hỏi trong 1 câu ──
        info_parts = []

        # Tồn kho
        if any(w in last_norm for w in stock_keywords) or (starts_with_con and ends_with_check):
            stock = drink_data["stock"]
            if stock == 0:
                status = "❌ Đã hết hàng"
            elif stock < 10:
                status = f"⚠️ Sắp hết — còn {stock} sản phẩm"
            else:
                status = f"✅ Còn {stock} sản phẩm"
            info_parts.append(f"📦 Tồn kho: {status}")

        # Hương vị — bắt cả "vị của X như thế nào", "X như thế nào"
        flavor_keywords = [
            "huong vi", "mui vi", "vi nhu the nao", "ngon khong",
            "ngot khong", "dang khong", "chua khong", "co vi gi",
            "vi gi", "co ngon khong", "uong co ngon",
            "vi cua", "vi the nao", "vi ra sao", "vi nao", "nhu the nao",
        ]
        if any(w in last_norm for w in flavor_keywords):
            info_parts.append(f"😋 Hương vị: {drink_data['flavor']}")

        # Dung tích / size
        if any(w in last_norm for w in ["size", "dung tich", "the tich", "lon hay chai",
                                         "may loai", "bao nhieu ml", "co may size", "co may dung tich"]):
            vols_str = ", ".join(f"{v}: {drink_data['price'][v]:,}đ" for v in drink_data["volumes"])
            info_parts.append(f"📦 Dung tích & Giá: {vols_str}")

        # Thương hiệu
        if any(w in last_norm for w in ["thuong hieu", "san xuat boi", "xuat xu",
                                         "cua hang nao", "cong ty nao", "hang nao", "brand"]):
            info_parts.append(f"🏭 Thương hiệu: {drink_data['brand']}")

        # Đặc điểm / công dụng
        if any(w in last_norm for w in ["dac diem", "cong dung", "tot khong", "co tot khong",
                                         "tac dung", "loi ich", "mo ta", "gioi thieu"]):
            info_parts.append(f"✨ Đặc điểm: {drink_data['features']}")

        # Hạn sử dụng
        if any(w in last_norm for w in ["han su dung", "han dung", "het han", "bao lau su dung", "date"]):
            info_parts.append(f"📅 Hạn sử dụng: {drink_data['expiry_months']} tháng kể từ ngày sản xuất")

        # Caffeine
        if any(w in last_norm for w in ["caffeine", "cafein"]):
            val = "☕ Có chứa caffeine." if drink_data["has_caffeine"] else "✅ Không có caffeine."
            info_parts.append(val)

        # Đường
        if any(w in last_norm for w in ["co duong", "duong khong", "co ngot", "it duong", "nhieu duong"]):
            val = "🍬 Có đường." if drink_data["has_sugar"] else "✅ Không đường / ít đường tự nhiên."
            info_parts.append(val)

        # Ga
        if any(w in last_norm for w in ["co ga", "co gas", "nuoc co ga", "gas khong", "ga khong"]):
            has_gas = drink_data.get("category") in ["Nước ngọt có ga", "Nước tăng lực"]
            val = "🫧 Có ga." if has_gas else "✅ Không có ga."
            info_parts.append(val)

        # Slogan / phương châm — lấy từ field "features" (đây là slogan/phương châm sản phẩm)
        if any(w in last_norm for w in ["slogan", "phuong cham", "khau hieu"]):
            slogan = drink_data.get("features", "Không có thông tin slogan.")
            info_parts.append(f"🎯 Slogan / Phương châm: {slogan}")

        # Độ phổ biến / bán chạy (riêng lẻ cho 1 sản phẩm)
        if any(w in last_norm for w in ["do pho bien", "pho bien", "ban chay", "ua chuong",
                                         "duoc mua nhieu", "rating", "danh gia", "xep hang",
                                         "luot ban", "da ban bao nhieu"]):
            stars = "⭐" * int(drink_data["popularity"])
            info_parts.append(
                f"📊 Độ phổ biến: {stars} ({drink_data['popularity']}/10)\n"
                f"🛒 Đã bán: {drink_data['sales']:,} sản phẩm"
            )

        # Sản phẩm mới
        if any(w in last_norm for w in ["san pham moi", "hang moi", "co moi khong", "moi khong", "moi ra"]):
            val = "🆕 Đây là sản phẩm MỚI!" if drink_data["is_new"] else "✅ Sản phẩm quen thuộc, không phải hàng mới."
            info_parts.append(val)

        # ── Tổng hợp kết quả ──
        if info_parts:
            header = f"{drink_data['image']} **{drink_data['name']}**"
            msg = header + "\n" + "\n".join(info_parts)
        else:
            # Fallback: hiển thị toàn bộ thông tin tổng quan
            new_badge = " 🆕" if drink_data["is_new"] else ""
            vols_str = ", ".join(f"{v}: {drink_data['price'][v]:,}đ" for v in drink_data["volumes"])
            msg = (
                f"{drink_data['image']} **{drink_data['name']}**{new_badge}\n"
                f"🏭 Thương hiệu: {drink_data['brand']}\n"
                f"📦 Dung tích & Giá: {vols_str}\n"
                f"😋 Hương vị: {drink_data['flavor']}\n"
                f"✨ Đặc điểm: {drink_data['features']}"
            )

        dispatcher.utter_message(text=msg)
        return []


# ============================================================
# CART-BASED ORDER FLOW
# ============================================================

class ActionAddToCart(Action):
    """
    Thêm sản phẩm vào giỏ hàng khi khách đặt món.
    Hỗ trợ nhiều sản phẩm trong 1 câu (vd: "cho 2 coca và 1 pepsi").
    Chỉ thông báo đã thêm, KHÔNG hỏi thêm hay gợi ý gì thêm.
    """
    def name(self) -> Text:
        return "action_add_to_cart"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        last_msg = tracker.latest_message.get("text", "")
        size_slot = tracker.get_slot("size")
        cart = get_cart(tracker)

        # Tìm tất cả sản phẩm + số lượng trong câu nói
        found_items = find_all_drinks_from_message(last_msg)

        # Fallback: nếu tìm không được từ message, dùng slot
        if not found_items:
            drink_slot = tracker.get_slot("drink")
            key, drink_data = find_drink(drink_slot)
            if not drink_data:
                dispatcher.utter_message(
                    text="❌ Tôi không tìm thấy sản phẩm này. Gõ 'menu' để xem danh sách nhé!"
                )
                return []
            qty_slot = tracker.get_slot("quantity") or "1"
            qty = parse_quantity(qty_slot)
            found_items = [(key, drink_data, qty)]

        added_lines = []
        last_key = None
        for key, drink_data, qty in found_items:
            if drink_data["stock"] == 0:
                dispatcher.utter_message(
                    text=f"😔 **{drink_data['name']}** đã hết hàng. Bạn muốn chọn sản phẩm khác không?"
                )
                continue

            volume = resolve_volume(drink_data, size_slot)
            unit_price = drink_data["price"].get(volume, list(drink_data["price"].values())[0])
            subtotal = unit_price * qty

            # Cộng dồn nếu sản phẩm đã có trong giỏ
            existing = next((item for item in cart if item["key"] == key and item["volume"] == volume), None)
            if existing:
                existing["qty"] += qty
                existing["subtotal"] = existing["qty"] * existing["unit_price"]
            else:
                cart.append({
                    "key": key,
                    "name": drink_data["name"],
                    "image": drink_data["image"],
                    "volume": volume,
                    "qty": qty,
                    "unit_price": unit_price,
                    "subtotal": subtotal,
                })
            added_lines.append(f"  ✅ {drink_data['image']} {qty} × {drink_data['name']} ({volume})")
            last_key = key

        if not added_lines:
            return []

        # Chỉ thông báo đã thêm, không gợi ý thêm
        msg = "➕ **Đã thêm vào giỏ hàng:**\n" + "\n".join(added_lines)
        dispatcher.utter_message(text=msg)

        return [
            SlotSet("cart", json.dumps(cart, ensure_ascii=False)),
            SlotSet("drink", last_key),
        ]


class ActionShowCart(Action):
    """Hiển thị giỏ hàng hiện tại."""
    def name(self) -> Text:
        return "action_show_cart"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        cart = get_cart(tracker)
        if not cart:
            dispatcher.utter_message(text="🛒 Giỏ hàng của bạn đang trống. Hãy chọn đồ uống nhé!")
            return []
        msg = format_cart(cart) + (
            "\n\n💬 Gõ 'xác nhận' để đặt hàng hoặc thêm sản phẩm khác!"
        )
        dispatcher.utter_message(text=msg)
        return []


class ActionConfirmOrder(Action):
    """
    Xác nhận đơn hàng: tổng kết giỏ hàng và hỏi phương thức thanh toán.
    Được gọi khi khách nói 'xác nhận', 'đặt hàng', 'ok',... 
    """
    def name(self) -> Text:
        return "action_confirm_order"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        cart = get_cart(tracker)

        # Nếu giỏ trống, thử lấy từ slot cũ (tương thích flow cũ)
        if not cart:
            drink_slot = tracker.get_slot("drink")
            _, drink_data = find_drink(drink_slot)
            if drink_data:
                size_slot = tracker.get_slot("size") or drink_data["default_volume"]
                qty = parse_quantity(tracker.get_slot("quantity") or "1")
                unit_price = drink_data["price"].get(size_slot, list(drink_data["price"].values())[0])
                cart = [{
                    "key": drink_slot,
                    "name": drink_data["name"],
                    "image": drink_data["image"],
                    "volume": size_slot,
                    "qty": qty,
                    "unit_price": unit_price,
                    "subtotal": unit_price * qty,
                }]
            else:
                dispatcher.utter_message(text="⚠️ Giỏ hàng trống! Hãy chọn sản phẩm trước nhé.")
                return []

        total = cart_total(cart)
        cart_display = format_cart(cart)

        msg = (
            f"✅ **XÁC NHẬN ĐƠN HÀNG**\n\n"
            f"{cart_display}\n\n"
            f"💳 Bạn muốn thanh toán bằng cách nào?\n\n"
            f"1️⃣ **Chuyển khoản** - Quét mã QR\n"
            f"2️⃣ **Tiền mặt** - Cho tiền vào khe máy\n"
            f"3️⃣ **Quẹt thẻ** - Đưa thẻ vào máy\n\n"
            f"👉 Vui lòng cho biết phương thức thanh toán!"
        )
        dispatcher.utter_message(text=msg)
        return [SlotSet("cart", json.dumps(cart, ensure_ascii=False))]


class ActionProcessPayment(Action):
    """
    Xử lý thanh toán - nhận diện nhiều cụm từ đồng nghĩa.
    Chỉ được gọi sau khi khách đã xác nhận đơn hàng.
    """
    def name(self) -> Text:
        return "action_process_payment"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        last_msg = tracker.latest_message.get("text", "")
        payment_slot = tracker.get_slot("payment_method") or ""

        method = detect_payment_method(last_msg)
        if not method:
            method = detect_payment_method(payment_slot)

        cart = get_cart(tracker)
        total_price = cart_total(cart) if cart else int(tracker.get_slot("total_price") or 0)

        if method == "qr":
            qr = BANK_QR_INFO
            msg = (
                f"📲 **THANH TOÁN CHUYỂN KHOẢN / QR**\n"
                f"{'═' * 35}\n"
                f"{qr['qr_text']}\n\n"
                f"💰 Số tiền cần chuyển: **{total_price:,}đ**\n"
                f"{'═' * 35}\n"
                f"⏳ Sau khi chuyển khoản thành công,\n"
                f"   máy sẽ tự động cấp sản phẩm!\n"
                f"📞 Liên hệ hỗ trợ nếu không nhận được hàng."
            )

        elif method == "cash":
            msg = (
                f"💵 **THANH TOÁN TIỀN MẶT**\n"
                f"{'═' * 35}\n"
                f"💰 Số tiền cần trả: **{total_price:,}đ**\n\n"
                f"👇 Vui lòng cho tiền vào khe nhận tiền\n"
                f"   phía bên phải máy.\n\n"
                f"ℹ️ Máy hỗ trợ tờ: 5K, 10K, 20K, 50K, 100K, 200K, 500K\n"
                f"⚡ Máy sẽ trả lại tiền thừa tự động!"
            )

        elif method == "card":
            msg = (
                f"💳 **THANH TOÁN QUẸT THẺ**\n"
                f"{'═' * 35}\n"
                f"💰 Số tiền: **{total_price:,}đ**\n\n"
                f"👇 Vui lòng đưa thẻ vào khe đọc thẻ\n"
                f"   phía bên trái máy.\n\n"
                f"✅ Hỗ trợ: Visa, Mastercard, ATM nội địa\n"
                f"⏳ Chờ máy xác nhận giao dịch..."
            )

        elif method == "pay":
            # Khách nói "thanh toán" nhưng chưa chỉ định phương thức → hỏi lại
            dispatcher.utter_message(
                text=(
                    f"💳 Bạn muốn thanh toán **{total_price:,}đ** bằng cách nào?\n\n"
                    f"1️⃣ **Chuyển khoản** — Quét mã QR\n"
                    f"2️⃣ **Tiền mặt** — Cho tiền vào khe máy\n"
                    f"3️⃣ **Quẹt thẻ** — Đưa thẻ vào máy"
                )
            )
            return []

        else:
            dispatcher.utter_message(
                text=(
                    f"❓ Tôi chưa hiểu phương thức thanh toán bạn chọn.\n"
                    f"Vui lòng chọn:\n"
                    f"1️⃣ **Chuyển khoản** (quét mã QR)\n"
                    f"2️⃣ **Tiền mặt** (cho tiền vào máy)\n"
                    f"3️⃣ **Quẹt thẻ** (đưa thẻ vào máy)"
                )
            )
            return []

        dispatcher.utter_message(text=msg)
        dispatcher.utter_message(
            text=(
                f"\n🎉 **Thanh toán thành công!**\n"
                f"🥤 Đồ uống đang được cấp phát...\n"
                f"Cảm ơn bạn đã sử dụng dịch vụ! 😊"
            )
        )

        return [
            SlotSet("cart", None),
            SlotSet("drink", None),
            SlotSet("size", None),
            SlotSet("quantity", "1"),
            SlotSet("payment_method", None),
            SlotSet("price_per_unit", None),
            SlotSet("total_price", None),
        ]


class ActionResetOrder(Action):
    def name(self) -> Text:
        return "action_reset_order"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        dispatcher.utter_message(
            text=(
                "❌ Đã hủy đơn hàng.\n\n"
                "Bạn muốn:\n"
                "🔄 Tiếp tục xem thông tin / đặt đồ uống khác? → Gõ tên sản phẩm hoặc 'menu'\n"
                "👋 Kết thúc? → Gõ 'tạm biệt'"
            )
        )
        return [
            SlotSet("cart", None),
            SlotSet("drink", None),
            SlotSet("size", None),
            SlotSet("quantity", "1"),
            SlotSet("payment_method", None),
            SlotSet("price_per_unit", None),
            SlotSet("total_price", None),
        ]


# Giữ lại các action cũ để tương thích với stories/rules hiện có

class ActionCalculatePrice(Action):
    """Tương thích ngược - tính giá cho 1 sản phẩm."""
    def name(self) -> Text:
        return "action_calculate_price"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        drink_slot = tracker.get_slot("drink")
        size_slot = tracker.get_slot("size")
        quantity_slot = tracker.get_slot("quantity") or "1"

        _, drink_data = find_drink(drink_slot)
        if not drink_data:
            return []

        volume = resolve_volume(drink_data, size_slot)
        quantity = parse_quantity(quantity_slot)
        price_per_unit = drink_data["price"].get(volume, list(drink_data["price"].values())[0])
        total = price_per_unit * quantity

        return [
            SlotSet("size", volume),
            SlotSet("quantity", str(quantity)),
            SlotSet("price_per_unit", str(price_per_unit)),
            SlotSet("total_price", str(total)),
        ]


class ActionShowOrderSummary(Action):
    """Tương thích ngược - hiển thị tóm tắt đơn 1 sản phẩm."""
    def name(self) -> Text:
        return "action_show_order_summary"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        drink_slot = tracker.get_slot("drink")
        size_slot = tracker.get_slot("size")
        quantity_slot = tracker.get_slot("quantity") or "1"
        total_price = tracker.get_slot("total_price") or "0"
        price_per_unit = tracker.get_slot("price_per_unit") or "0"

        _, drink_data = find_drink(drink_slot)
        if not drink_data:
            dispatcher.utter_message(text="⚠️ Chưa có sản phẩm nào trong đơn hàng.")
            return []

        quantity = parse_quantity(quantity_slot)
        msg = (
            f"🛒 **GIỎ HÀNG**\n"
            f"{'─' * 30}\n"
            f"{drink_data['image']} {drink_data['name']} ({size_slot})\n"
            f"   x{quantity} × {int(price_per_unit):,}đ = {int(total_price):,}đ\n"
            f"{'─' * 30}\n"
            f"💵 **Tổng cộng: {int(total_price):,}đ**\n"
            f"{'─' * 30}\n\n"
            f"💬 Bạn muốn:\n"
            f"  🛍️ Thêm sản phẩm khác → Nói tên sản phẩm\n"
            f"  ✅ Xác nhận đặt hàng → Gõ 'xác nhận'\n"
            f"  ❌ Hủy → Gõ 'hủy'"
        )
        dispatcher.utter_message(text=msg)
        return []


class ActionAskQuantity(Action):
    def name(self) -> Text:
        return "action_ask_quantity"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        drink_slot = tracker.get_slot("drink")
        _, drink_data = find_drink(drink_slot)
        drink_name = drink_data["name"] if drink_data else "sản phẩm"
        dispatcher.utter_message(text=f"🔢 Bạn muốn mua bao nhiêu **{drink_name}**?")
        return []


class ActionRecommendDrink(Action):
    def name(self) -> Text:
        return "action_recommend_drink"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        last_norm = normalize_text(tracker.latest_message.get("text", ""))

        # Hỏi bán chạy nhất → top 3 theo sales
        if any(w in last_norm for w in ["ban chay", "nhieu nguoi mua", "mua nhieu nhat", "ban nhieu"]):
            top3 = sorted(DRINKS_DB.items(), key=lambda x: x[1]["sales"], reverse=True)[:3]
            lines = ["🏆 **TOP 3 BÁN CHẠY NHẤT**\n" + "─" * 32]
            for i, (key, d) in enumerate(top3, 1):
                lines.append(f"  {i}. {d['image']} {d['name']} — Đã bán: {d['sales']:,} sản phẩm")
            lines.append("\n💬 Bạn muốn chọn loại nào?")
            dispatcher.utter_message(text="\n".join(lines))
            return []

        # Hỏi phổ biến nhất → top 3 theo popularity
        if any(w in last_norm for w in ["pho bien", "duoc ua chuong", "nhieu nguoi thich", "noi tieng", "pho bien nhat"]):
            top3 = sorted(DRINKS_DB.items(), key=lambda x: x[1]["popularity"], reverse=True)[:3]
            lines = ["🌟 **TOP 3 PHỔ BIẾN NHẤT**\n" + "─" * 32]
            for i, (key, d) in enumerate(top3, 1):
                lines.append(f"  {i}. {d['image']} {d['name']} — Độ phổ biến: {d['popularity']}/10")
            lines.append("\n💬 Bạn muốn chọn loại nào?")
            dispatcher.utter_message(text="\n".join(lines))
            return []

        # Gợi ý chung → top 5 theo popularity
        top5 = sorted(DRINKS_DB.items(), key=lambda x: x[1]["popularity"], reverse=True)[:5]
        lines = ["🌟 **GỢI Ý ĐỒ UỐNG HÔM NAY**\n" + "─" * 35]
        for i, (key, d) in enumerate(top5, 1):
            default_vol = d["default_volume"]
            price = d["price"][default_vol]
            lines.append(f"  {i}. {d['image']} {d['name']} - {price:,}đ\n     👉 {d['flavor']}")
        lines.append("\n💬 Bạn muốn chọn loại nào?")
        dispatcher.utter_message(text="\n".join(lines))
        return []


class ActionCheckPromotion(Action):
    def name(self) -> Text:
        return "action_check_promotion"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        msg = (
            "🎉 **KHUYẾN MÃI HIỆN TẠI**\n"
            "─" * 35 + "\n"
            "🔥 Mua 2 tặng 1 với Sting và Number 1!\n"
            "💚 Giảm 10% khi mua từ 5 sản phẩm trở lên\n"
            "🆕 Sản phẩm mới: Aloe Vera & Trà Gạo Lứt giảm 15%\n"
            "📅 Khuyến mãi áp dụng đến cuối tháng\n\n"
            "Bạn muốn đặt ngay không? 😊"
        )
        dispatcher.utter_message(text=msg)
        return []


class ActionHandleOutOfDomain(Action):
    def name(self) -> Text:
        return "action_handle_out_of_domain"

    def run(self, dispatcher: CollectingDispatcher, tracker: Tracker, domain: Dict[Text, Any]) -> List[Dict[Text, Any]]:
        import random
        responses = [
            (
                "🚫 Xin lỗi, câu hỏi này nằm ngoài khả năng của tôi.\n"
                "Tôi chỉ là máy bán nước tự động, chỉ hỗ trợ:\n\n"
                "🍹 Đặt đồ uống\n"
                "💰 Hỏi giá sản phẩm\n"
                "🧪 Hỏi thành phần / thông tin\n"
                "⭐ Gợi ý đồ uống\n\n"
                "👉 Gõ 'menu' để xem danh sách hoặc cho tôi biết bạn muốn uống gì!"
            ),
            (
                "🤖 Tôi không thể trả lời câu đó vì tôi chỉ phục vụ đồ uống thôi!\n\n"
                "Bạn có muốn:\n"
                "• Xem menu → gõ 'menu'\n"
                "• Đặt nước → nói tên sản phẩm\n"
                "• Hỏi giá → 'giá [tên nước]'"
            ),
            (
                "⚠️ Câu hỏi này ngoài phạm vi của tôi.\n"
                "Tôi chỉ hỗ trợ thông tin về đồ uống trong máy.\n\n"
                "👉 Thử hỏi: 'Coca giá bao nhiêu?' hoặc 'Có nước gì?'"
            ),
        ]
        dispatcher.utter_message(text=random.choice(responses))
        return []