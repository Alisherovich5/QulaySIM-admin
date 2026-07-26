"""Seed landing-page CMS content (benefits, testimonials, devices, promo, FAQ)
in all three languages. Idempotent — safe to run repeatedly.

    ./venv/bin/python manage.py shell -c "exec(open('seed_content.py').read())"
or  ./venv/bin/python seed_content.py
"""
import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from content.models import FAQ, Benefit, Device, PromoBanner, Testimonial  # noqa: E402

BENEFITS = [
    {
        "icon": "Globe2",
        "title": "The future of connectivity",
        "text": "A fully digital SIM you can install in minutes — no shops, no waiting, no plastic.",
        "title_ru": "Будущее связи",
        "text_ru": "Полностью цифровая SIM, которую можно установить за минуты — без магазинов, ожидания и пластика.",
        "title_uz": "Aloqaning kelajagi",
        "text_uz": "Bir necha daqiqada o‘rnatiladigan to‘liq raqamli SIM — do‘konsiz, kutishsiz, plastik kartasiz.",
    },
    {
        "icon": "Radio",
        "title": "Borderless communication",
        "text": "Use an eSIM for travel data while keeping your primary SIM and number available for calls.",
        "title_ru": "Связь без границ",
        "text_ru": "Используйте eSIM для интернета в поездке, сохраняя основную SIM-карту и номер для звонков.",
        "title_uz": "Chegarasiz aloqa",
        "text_uz": "Sayohatda internet uchun eSIM‘dan foydalaning, asosiy SIM raqamingizni esa qo‘ng‘iroqlar uchun saqlab qoling.",
    },
    {
        "icon": "Zap",
        "title": "Effortless setup",
        "text": "Receive your QR code after purchase, install it in your phone settings, and activate it when you arrive.",
        "title_ru": "Простая настройка",
        "text_ru": "Получите QR-код после покупки, установите eSIM через настройки телефона и активируйте по прибытии.",
        "title_uz": "Oson sozlash",
        "text_uz": "Xariddan keyin QR-kodni oling, telefon sozlamalari orqali o‘rnating va manzilga yetganda faollashtiring.",
    },
    {
        "icon": "Wallet",
        "title": "Flexible, budget-friendly plans",
        "text": "Pay only for the data you need with transparent per-plan pricing and no hidden fees.",
        "title_ru": "Гибкие и доступные тарифы",
        "text_ru": "Платите только за нужный трафик — прозрачные цены, без скрытых комиссий.",
        "title_uz": "Moslashuvchan, hamyonbop tariflar",
        "text_uz": "Faqat kerakli internet uchun to‘lang — shaffof narx, yashirin to‘lovlarsiz.",
    },
]

TESTIMONIALS = [
    {
        "name": "Aziza K.",
        "rating": 5,
        "location": "Tashkent → Tokyo",
        "text": "Activated my eSIM before the plane even landed. Data worked the second I switched off airplane mode.",
        "location_ru": "Ташкент → Токио",
        "text_ru": "Активировала eSIM ещё до посадки. Интернет заработал, как только я выключила авиарежим.",
        "location_uz": "Toshkent → Tokio",
        "text_uz": "Samolyot qo‘nmasdanoq eSIM‘imni faollashtirdim. Aviarejimni o‘chirgan zahotim internet ishladi.",
    },
    {
        "name": "Daniel M.",
        "rating": 5,
        "location": "Berlin → Dubai",
        "text": "So much cheaper than roaming and setup took under a minute. The app makes topping up effortless.",
        "location_ru": "Берлин → Дубай",
        "text_ru": "Намного дешевле роуминга, а настройка заняла меньше минуты. Пополнять через приложение очень удобно.",
        "location_uz": "Berlin → Dubay",
        "text_uz": "Roumingdan ancha arzon va sozlash bir daqiqadan kam vaqt oldi. Ilova to‘ldirishni juda osonlashtiradi.",
    },
    {
        "name": "Sofia R.",
        "rating": 5,
        "location": "Madrid → Bangkok",
        "text": "One app for every trip. I keep my number for calls and use the eSIM for data. Brilliant.",
        "location_ru": "Мадрид → Бангкок",
        "text_ru": "Одно приложение на все поездки. Номер оставляю для звонков, а eSIM — для интернета. Отлично.",
        "location_uz": "Madrid → Bangkok",
        "text_uz": "Har bir sayohat uchun bitta ilova. Qo‘ng‘iroqlar uchun raqamimni saqlayman, internet uchun eSIM. Zo‘r.",
    },
]

DEVICES = [
    "iPhone XS / 11 / 12 / 13 / 14 / 15",
    "Google Pixel 3 and newer",
    "Samsung Galaxy S20 / S21 / S22 / S23",
    "Samsung Galaxy Z Flip / Fold",
    "iPad Pro / Air (2018+)",
    "Huawei P40 / Mate 40 Pro",
]

FAQS = [
    {
        "category": "general",
        "question": "What is an eSIM?",
        "answer": "An eSIM is a digital SIM that lets you activate a mobile data plan without a physical card. You scan a QR code and you are connected.",
        "question_ru": "Что такое eSIM?",
        "answer_ru": "eSIM — это цифровая SIM-карта, позволяющая активировать мобильный тариф без физической карты. Вы сканируете QR-код и подключаетесь.",
        "question_uz": "eSIM nima?",
        "answer_uz": "eSIM — bu jismoniy kartasiz mobil internet tarifini faollashtirishga imkon beruvchi raqamli SIM. QR-kodni skanerlaysiz va ulanasiz.",
    },
    {
        "category": "setup",
        "question": "How do I install my QulaySIM eSIM?",
        "answer": "After purchase, open My eSIMs, scan the QR code with your phone camera or add it in Settings > Cellular > Add eSIM. Activate it when you arrive at your destination.",
        "question_ru": "Как установить мой eSIM от QulaySIM?",
        "answer_ru": "После покупки откройте «Мои eSIM», отсканируйте QR-код камерой телефона или добавьте его в Настройки > Сотовая связь > Добавить eSIM. Активируйте по прибытии.",
        "question_uz": "QulaySIM eSIM‘imni qanday o‘rnataman?",
        "answer_uz": "Xariddan so‘ng “Mening eSIM‘larim”ni oching, QR-kodni telefon kamerangiz bilan skanerlang yoki Sozlamalar > Uyali aloqa > eSIM qo‘shish orqali qo‘shing. Yetib borganingizda faollashtiring.",
    },
    {
        "category": "device",
        "question": "Is my phone compatible?",
        "answer": "Most phones released after 2018 support eSIM (iPhone XS+, Pixel 3+, recent Samsung Galaxy S/Note/Z). Your device must also be carrier-unlocked.",
        "question_ru": "Совместим ли мой телефон?",
        "answer_ru": "Большинство телефонов, выпущенных после 2018 года, поддерживают eSIM (iPhone XS+, Pixel 3+, новые Samsung Galaxy S/Note/Z). Устройство также должно быть разблокировано.",
        "question_uz": "Telefonim mos keladimi?",
        "answer_uz": "2018-yildan keyin chiqqan aksariyat telefonlar eSIM‘ni qo‘llab-quvvatlaydi (iPhone XS+, Pixel 3+, yangi Samsung Galaxy S/Note/Z). Qurilmangiz operatorga bog‘lanmagan bo‘lishi ham kerak.",
    },
    {
        "category": "billing",
        "question": "Which payment methods do you accept?",
        "answer": "This demo build uses a mock payment gateway. Production will support international cards and local providers.",
        "question_ru": "Какие способы оплаты вы принимаете?",
        "answer_ru": "В этой демо-версии используется тестовый платёжный шлюз. В продакшене будут поддерживаться международные карты и местные провайдеры.",
        "question_uz": "Qanday to‘lov usullarini qabul qilasiz?",
        "answer_uz": "Ushbu demo versiyada test to‘lov tizimi ishlatiladi. Ishlab chiqarishda xalqaro kartalar va mahalliy provayderlar qo‘llab-quvvatlanadi.",
    },
    {
        "category": "general",
        "question": "Can I top up my plan?",
        "answer": "Yes — when your data runs low you can purchase an add-on without changing your eSIM.",
        "question_ru": "Могу ли я пополнить тариф?",
        "answer_ru": "Да — когда трафик заканчивается, вы можете купить дополнительный пакет, не меняя eSIM.",
        "question_uz": "Tarifimni to‘ldira olamanmi?",
        "answer_uz": "Ha — internetingiz tugab qolganda eSIM‘ni o‘zgartirmasdan qo‘shimcha paket sotib olishingiz mumkin.",
    },
]

PROMO = {
    "code": "WELCOME10",
    "cta_link": "/destinations",
    "eyebrow": "Welcome bonus",
    "title": "Get 10% off your first eSIM",
    "text": "Use code {{code}} at checkout for an instant discount on your first data plan.",
    "eyebrow_ru": "Приветственный бонус",
    "title_ru": "Скидка 10% на первый eSIM",
    "text_ru": "Введите код {{code}} при оформлении и получите мгновенную скидку на первый тариф.",
    "eyebrow_uz": "Xush kelibsiz bonusi",
    "title_uz": "Birinchi eSIM‘ingizga 10% chegirma",
    "text_uz": "To‘lovda {{code}} kodini kiriting va birinchi tarifingizga darhol chegirma oling.",
}


def run():
    for i, b in enumerate(BENEFITS):
        Benefit.objects.update_or_create(title=b["title"], defaults={**b, "sort_order": i})
    for i, tm in enumerate(TESTIMONIALS):
        Testimonial.objects.update_or_create(
            name=tm["name"],
            defaults={**tm, "sort_order": i, "moderation_status": "approved"},
        )
    for i, name in enumerate(DEVICES):
        Device.objects.update_or_create(name=name, defaults={"sort_order": i, "is_active": True})
    for i, f in enumerate(FAQS):
        FAQ.objects.update_or_create(question=f["question"], defaults={**f, "sort_order": i})
    PromoBanner.objects.update_or_create(code=PROMO["code"], defaults={**PROMO, "is_active": True})
    print(
        f"Seeded: {Benefit.objects.count()} benefits, {Testimonial.objects.count()} testimonials, "
        f"{Device.objects.count()} devices, {FAQ.objects.count()} faqs, "
        f"{PromoBanner.objects.count()} promo banner(s)."
    )


if __name__ == "__main__":
    run()
