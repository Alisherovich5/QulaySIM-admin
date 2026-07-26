# QulaySIM-admin — To'liq Texnik Audit

Sana: 2026-07-26 · Commit: `1351fb7` (yagona commit) · 2 375 qator Python
Muhit: Python 3.12, Django 5.2.16, django-unfold 0.101.0

---

## 0. Redis, Celery, Docker — bu repoda ham yo'q

`requirements.txt` to'liq holicha:
```
Django>=5.1,<5.3
psycopg[binary]>=3.1
django-unfold>=0.40
python-decouple>=3.8
Pillow>=10.0
```

Butun repo bo'ylab `celery`, `redis`, `CACHES`, `docker` so'zlari **bir marta ham
uchramaydi**. Bu sof Django admin paneli — API ham yo'q (`urls.py` da faqat
`/admin/`, to'rtta `views.py` bo'sh shablon).

## Haqiqiy arxitektura

`config/settings.py` docstring'i aniq aytadi:

> Django is the SCHEMA OWNER for the whole platform: all models and migrations
> live here, and the FastAPI service reads/writes the same PostgreSQL tables via
> SQLAlchemy.

`seed_orders.py:43` da yana bir ishora: `"No plans — run backend-api/seed.py first."`

Ya'ni platforma **uch repodan** iborat:

```
QulaySIM (React)  ──HTTP──▶  backend-api (FastAPI + SQLAlchemy)  ──┐
                                                                   ├──▶ PostgreSQL
QulaySIM-admin (Django, schema owner) ─────────────────────────────┘
```

**`backend-api` reposi menda yo'q.** Redis/Celery/Docker qayerdadir bo'lsa —
o'sha yerda. Uni yuborsangiz, uchinchi qismni ham xuddi shunday tahlil qilaman.
Hozircha shu Django qismi.

---

## 1. Xavfsizlik — eng jiddiy blok

### 1.1 Xavfli default'lar zanjiri — P0 KRITIK

```python
SECRET_KEY   = config("SECRET_KEY", default="dev-secret-key")   # settings.py:16
DEBUG        = config("DEBUG", default=True, cast=bool)         # settings.py:17
ALLOWED_HOSTS= config("ALLOWED_HOSTS", default="*", cast=Csv()) # settings.py:18
DB_PASSWORD  = config("DB_PASSWORD", default="fastsim")         # settings.py:73
```

Har bir default alohida ham yomon, lekin ularni **bir xavfli zanjirga** bog'laydigan
narsa bor: **`.env.example` fayli mavjud emas**, garchi `.gitignore` unga aniq
havola qilsa ham (`!.env.example`). Ya'ni:

1. Deploy qiluvchi qaysi o'zgaruvchilar kerakligini bilmaydi
2. `.env` siz ishga tushiradi
3. Django **jim** `dev-secret-key`, `DEBUG=True`, `ALLOWED_HOSTS=*` bilan ko'tariladi
4. Hech qanday ogohlantirish chiqmaydi

Oqibatlari:
- **Ma'lum `SECRET_KEY`** → sessiya cookie'sini soxtalashtirish, parol tiklash
  tokenini yasash, `signing` API'sini buzish. Hujumchi istalgan superuser sifatida kiradi.
- **`DEBUG=True`** → har bir 500 xatosida to'liq stack trace, barcha settings
  qiymatlari (DB paroli ham!), SQL so'rovlar tarixi brauzerga chiqadi.
- **`ALLOWED_HOSTS=*`** → Host header injection, parol tiklash havolalarini
  hujumchi domeniga yo'naltirish.

**Tuzatish:** `default=` larni olib tashlash (`config("SECRET_KEY")` — yo'q bo'lsa
`UndefinedValueError` bilan yiqilsin, bu **to'g'ri** xatti-harakat), `DEBUG` default
`False`, `.env.example` yozish.

### 1.2 `check --deploy` natijasi

```
security.W004  SECURE_HSTS_SECONDS o'rnatilmagan
security.W008  SECURE_SSL_REDIRECT True emas
security.W009  SECRET_KEY juda qisqa / zaif
security.W012  SESSION_COOKIE_SECURE True emas
security.W016  CSRF_COOKIE_SECURE True emas
```

Beshtasi ham bir blok bilan tuzatiladi:

```python
if not DEBUG:
    SECURE_SSL_REDIRECT = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    CSRF_TRUSTED_ORIGINS = config("CSRF_TRUSTED_ORIGINS", cast=Csv())
```

### 1.3 Logging umuman yo'q
`LOGGING` konfiguratsiyasi yozilmagan. Production'da xatolar hech qayerga
yozilmaydi — na faylga, na Sentry'ga. Buzilish sodir bo'lsa, iz qolmaydi.

### 1.4 Ijobiy tomon
`format_html()` hamma joyda to'g'ri ishlatilgan — badge, progress bar, flag
render'larida XSS yo'q. `templates/admin/index.html` da `|safe` yoki
`autoescape off` **umuman yo'q**. Bu yaxshi intizom.

---

## 2. Performance — o'lchandi, taxmin emas

30 qatorli test ma'lumoti bilan admin list sahifalarining SQL so'rovlarini sanadim:

| Admin | Qator | SQL so'rov | Muammo |
|---|---|---|---|
| `CountryAdmin` | 30 | **32** | `from_price` → `starting_price` property har qator uchun so'rov |
| `OrderAdmin` | 30 | **31** | `item_count` → `obj.items.count()` har qator uchun |
| `CustomerAdmin` | 30 | **31** | `order_count` → `obj.orders.count()` har qator uchun |
| `RegionAdmin` | 1 | 2 | `country_count` → `obj.countries.count()` har qator uchun |

Django admin'ning standart sahifa hajmi **100 qator** — ya'ni real hayotda bu
**~101 so'rov har bir sahifa ochilishida**.

### Tuzatishni ham o'lchadim

```python
def get_queryset(self, request):
    return super().get_queryset(request).annotate(_item_count=Count("items"))
```

| | Oldin | Keyin |
|---|---|---|
| CountryAdmin (30 qator) | 32 so'rov | **1 so'rov** |
| OrderAdmin (30 qator) | 31 so'rov | **1 so'rov** |

**32 barobar yaxshilanish**, har bir admin uchun 4 qatorlik o'zgarish.

### 2.1 Sidebar badge — har bir sahifada indekssiz COUNT

```python
def _badge_pending_orders(request):          # settings.py:103
    return str(Order.objects.filter(status=Order.Status.PENDING).count())
```

Bu funksiya **har bir admin sahifasi** render'ida chaqiriladi (sidebar navigatsiya
qismi), va `orders_order.status` da **indeks yo'q** → jadval to'liq skanerlanadi.
100k buyurtmada har bosishda sezilarli kechikish.

### 2.2 Dashboard — 10 so'rov, keshsiz
`dashboard_callback` har safar 10 ta agregat so'rov bajaradi (revenue, 14 kunlik
chart, top-5 davlat, 6 ta count). Kesh yo'q. Bosh sahifa har yangilanganda qaytadan
hisoblanadi. `cache.get_or_set(..., 300)` bilan o'rash kerak — lekin `CACHES`
sozlanmagan (default `LocMemCache`, ko'p-worker'da har worker o'z keshiga ega,
foydasiz). **Aynan shu yerda Redis kerak bo'ladi.**

### 2.3 Yetishmayotgan indekslar

Mavjud indekslarni bazadan o'qidim — faqat FK indekslari va `unique=True`
constraint'lari bor. Quyidagilar **yo'q**:

| Ustun | Kim ishlatadi |
|---|---|
| `orders_order.status` | Sidebar badge (har sahifada), dashboard, admin filter |
| `orders_order.paid_at` | Dashboard 14 kunlik oyna |
| `orders_esim.status` | Dashboard `active_esims` |
| `catalog_country.is_active` | Dashboard, FastAPI har `/countries` so'rovida |
| `catalog_plan.is_active` | `starting_price`, FastAPI |
| `catalog_country.iso2` | FastAPI iso2 bo'yicha qidiruv |

Yechim — har modelga `Meta.indexes`:
```python
class Meta:
    indexes = [
        models.Index(fields=["status", "-created_at"]),
        models.Index(fields=["paid_at"]),
    ]
```

### 2.4 `CONN_MAX_AGE` yo'q
`DATABASES["default"]` da `CONN_MAX_AGE` belgilanmagan → **har HTTP so'rovda
yangi PostgreSQL ulanishi ochiladi va yopiladi**. `CONN_MAX_AGE=60` yoki
pgbouncer bilan `CONN_MAX_AGE=0` + pooler.

### 2.5 `ESIM.qr_image` — bazada base64 PNG
```python
qr_image = models.TextField(blank=True, help_text="Base64 PNG data URL")
```
Har QR ~5-10 KB base64. 100 000 eSIM = **~1 GB jadval**. Oqibat: backup sekin,
replikatsiya og'ir, `SELECT *` qimmat, admin list sahifasi ham. Fayl saqlashga
(S3/MinIO) o'tish kerak. Qiziq: `Pillow` allaqachon `requirements.txt` da bor,
lekin **hech qayerda ishlatilmagan** — ehtimol shu maqsad uchun qo'shilib, keyin
unutilgan.

---

## 3. Ma'lumot yaxlitligi — empirik tasdiqlangan teshiklar

Har birini real bazaga yozib sinab ko'rdim. Hammasi **muvaffaqiyatli saqlandi**:

| # | Sinov | Natija |
|---|---|---|
| 1 | Ikkita davlat bir xil `iso2="JP"` bilan | ✗ Saqlandi — `unique` yo'q |
| 2 | `iso2="uz"` (kichik harf) | ✗ Qabul qilindi — normalizatsiya yo'q |
| 3 | `scope="local"`, `country=None`, `region=None` | ✗ Saqlandi — `CheckConstraint` yo'q |
| 4 | `price_usd = -99.99` | ✗ Saqlandi — manfiy narx |
| 5 | `subtotal=100, discount=10, total=999` | ✗ Saqlandi — arifmetika tekshirilmaydi |
| 6 | `max_uses=5, used_count=500` | ✗ Saqlandi — limitdan 100× oshiq |
| 7 | `rating=99` (5 balli tizimda) | ✗ Validatsiyadan o'tdi |

**№1 eng xavflisi** chunki frontend'ga bevosita ta'sir qiladi. React tomonida
`src/lib/isoNumeric.ts` `iso2 → ISO numeric` xaritasi bor va globus/bayroqlar
shunga tayanadi. Ikki davlat bir xil `iso2` bilan bo'lsa — xarita buziladi,
noto'g'ri bayroq ko'rsatiladi.

**№2** ham real: frontend `flagUrl()` da `.toLowerCase()`, `numericFor()` da
`.toUpperCase()` qiladi — ya'ni frontend ikkalasiga chidamli. Lekin FastAPI
`iso2` bo'yicha to'g'ridan-to'g'ri qidirsa, `"uz" != "UZ"` muammosi chiqadi.

Yechim namunasi:
```python
class Country(models.Model):
    iso2 = models.CharField(max_length=2, unique=True, db_index=True,
                            validators=[RegexValidator(r"^[A-Z]{2}$")])
    def save(self, *a, **kw):
        self.iso2 = self.iso2.upper()
        super().save(*a, **kw)

class Plan(models.Model):
    class Meta:
        constraints = [
            models.CheckConstraint(check=Q(price_usd__gte=0), name="plan_price_non_negative"),
            models.CheckConstraint(
                check=(Q(scope="local", country__isnull=False)
                       | Q(scope="regional", region__isnull=False)
                       | Q(scope="global")),
                name="plan_scope_target_match"),
        ]
```

---

## 4. Cross-service xavf — eng nozik arxitektura muammosi

Bu ikki-ORM sxemasining klassik tuzog'i va bu yerda **real mavjud**.

### 4.1 `auto_now_add` DB darajasida default bermaydi

Yaratilgan jadval SQL'ini o'qidim:
```sql
CREATE TABLE "customers_customer" (
  ...
  "created_at" datetime NOT NULL,     -- ← DEFAULT YO'Q
  ...
)
```

`auto_now_add=True` — bu **Django ORM darajasidagi** xatti-harakat, DB
constraint emas. Ya'ni SQLAlchemy tomonidan qilingan INSERT `created_at` ni
aniq bermasa → `NOT NULL violation` bilan yiqiladi.

Bu **6 ta modelga** tegishli: `Customer`, `Order`, `ESIM`, `Payment`,
`Referral`, `Testimonial`.

Django tomonda tuzatish:
```python
created_at = models.DateTimeField(auto_now_add=True, db_default=Now())
```
(`db_default` — Django 5.0+ da bor, aynan shu holat uchun qo'shilgan.)

### 4.2 `TextChoices` — DB'da CHECK constraint emas
`Order.Status`, `ESIM.Status`, `Payment.Status`, `PromoCode.DiscountType`,
`Plan.Scope`, `Plan.Network`, `Testimonial.ModerationStatus` — hammasi faqat
Django validatsiyasi. SQLAlchemy tomondan `status='banana'` yozish mumkin, DB
qabul qiladi, keyin Django admin `get_status_display()` da yiqiladi.

Yechim: har biriga `CheckConstraint(check=Q(status__in=[...]))`.

### 4.3 Sxema kontrakti himoyalanmagan
Django migratsiyasi ustunni o'zgartirsa, SQLAlchemy modellari jim eskiradi.
Buni ushlaydigan hech narsa yo'q: kontrakt testi yo'q, sxema snapshot'i yo'q,
CI yo'q. Ikki repo bir-biridan mustaqil deploy qilinadi.

Minimal himoya: `backend-api` CI'da `manage.py makemigrations --check` +
SQLAlchemy modellarini real sxemaga solishtiruvchi test.

---

## 5. Test va infratuzilma — deyarli nol

| Narsa | Holat |
|---|---|
| Testlar | **0 ta.** To'rtta `tests.py` ham 3 qatorlik bo'sh shablon |
| CI/CD | Yo'q |
| Docker | Yo'q |
| `.env.example` | Yo'q (lekin `.gitignore` unga havola qiladi) |
| README | **Yo'q** |
| Linter (ruff/flake8) | Yo'q |
| Formatter (black) | Yo'q |
| pre-commit | Yo'q |
| Git tarixi | **1 ta commit** — "Add QulaySIM admin panel source" |
| `views.py` × 4 | Bo'sh shablon, o'chirilishi kerak |

Bitta commit degani — kod tarixi yo'q, `git blame` foydasiz, review izlari yo'q.

Docker bu loyihada ayniqsa kerak: PostgreSQL + Django + FastAPI + (kelajakda)
Redis birga ishga tushishi kerak. Hozir har bir dasturchi qo'lda sozlaydi.

---

## 6. Nima yaxshi qilingan

Bu repo ham ko'p jihatdan puxta:

- **`db_table` har modelda aniq belgilangan** — SQLAlchemy bilan jadval nomlarini
  kelishish uchun to'g'ri va ongli qaror. Django'ning avtomatik nomlanishiga
  tayanmaslik bu yerda aynan to'g'ri.
- **`PROTECT` moliyaviy FK'larda** — `OrderItem.plan` va `ESIM.plan` `PROTECT`
  bilan. Tarif o'chirilsa buyurtma tarixi buzilmaydi. Bu senior qaror;
  ko'pchilik `CASCADE` qo'yib yuboradi.
- **`readonly_fields` moliyaviy maydonlarda** — `OrderAdmin` da `subtotal`,
  `discount`, `total` faqat o'qish uchun. Admin qo'lda pul miqdorini
  o'zgartira olmaydi.
- **`autocomplete_fields`** barcha katta FK'larda — `<select>` da 10 000 davlat
  yuklanmaydi.
- **Uch tilli content modellari** `fieldsets` bilan chiroyli ajratilgan
  (English / Русский / Oʻzbekcha) — kontent muharriri uchun aniq UX. Frontend'ning
  100% i18n pariteti aynan shundan kelib chiqadi.
- **Testimonial moderatsiyasi** — `pending/approved/rejected` + bulk action'lar.
  Foydalanuvchi sharhi to'g'ridan-to'g'ri saytga chiqmaydi.
- **Unfold integratsiyasi professional** — maxsus palitra, sidebar navigatsiya,
  pending-order badge, dashboard callback, `UserAdmin`/`GroupAdmin` qayta
  ro'yxatdan o'tkazilgan.
- **`manage.py check` toza, migration drift yo'q** — `makemigrations --check`
  hech qanday o'zgarish topmadi. Migratsiyalar model bilan sinxron.
- **Seed skriptlari idempotent** (`get_or_create`) — qayta-qayta ishga tushirish xavfsiz.

---

## 7. Prioritetlangan reja

### P0 — darhol (1 kun)
1. `SECRET_KEY` va `DEBUG` default'larini olib tashlash; `.env` majburiy qilish
2. `.env.example` yozish (barcha o'zgaruvchilar ro'yxati bilan)
3. `if not DEBUG:` xavfsizlik bloki (5 ta `check --deploy` ogohlantirishi)
4. `LOGGING` konfiguratsiyasi
5. To'rtta N+1 ni `annotate()` bilan tuzatish (32 so'rov → 1)

### P1 — shu hafta (2-3 kun)
6. `Country.iso2` → `unique=True` + `upper()` normalizatsiya (mavjud dublikatlarni tozalab)
7. `CheckConstraint` lar: manfiy narx, `total = subtotal - discount`,
   `used_count <= max_uses`, `rating 1..5`, `scope`/target mosligi
8. `Meta.indexes` — `status`, `paid_at`, `is_active`, `iso2`
9. `db_default=Now()` — 6 ta `created_at` maydoniga (cross-service INSERT xavfi)
10. `CONN_MAX_AGE = 60`
11. `_badge_pending_orders` va `dashboard_callback` ga kesh (60-300 s)
12. Bo'sh `views.py` fayllarini o'chirish

### P2 — keyingi sprint
13. **Redis** qo'shish — `CACHES` backend, dashboard va badge keshi uchun
    (hozir aynan shu yerda yetishmayapti)
14. `ESIM.qr_image` ni fayl saqlashga ko'chirish (`ImageField` + S3)
15. **Docker Compose** — postgres + redis + django + fastapi bitta buyruq bilan
16. `pytest-django` + kritik testlar: admin sahifalar 200 qaytarishi, moderation
    action'lari, dashboard agregatlari, constraint'lar
17. GitHub Actions: `check --deploy`, `makemigrations --check`, ruff, testlar
18. `ruff` + `black` + pre-commit
19. README (arxitektura diagrammasi bilan — uch repo aloqasi hujjatlashtirilishi shart)

### P3 — arxitektura
20. **Sxema kontrakti testi** — `backend-api` CI'da SQLAlchemy modellarini real
    Django sxemasiga solishtirish. Ikki-ORM sxemasi buni talab qiladi.
21. `django-simple-history` yoki audit log — buyurtma/to'lov o'zgarishlari izi
22. **Celery** — agar eSIM provideri (`esimaccess`) bilan async ishlash,
    webhook qayta urinishlari, kunlik hisobotlar kerak bo'lsa. Hozir `provider`
    maydonlari bor (`provider_esim_tran_no`, `provider_order_no`,
    `provider_status`) lekin ularni yangilaydigan fon jarayoni **yo'q** — ya'ni
    provider statusi hech qachon sinxronlanmaydi. Bu Celery uchun aniq ehtiyoj.
23. Sentry

---

## 8. Umumiy baho

| Jihat | Baho | Izoh |
|---|---|---|
| Model dizayni | 7/10 | Toza, `PROTECT` to'g'ri, lekin constraint'lar yo'q |
| Admin UX | 9/10 | Unfold integratsiyasi professional |
| Xavfsizlik | 3/10 | Default'lar zanjiri + `.env.example` yo'qligi |
| Performance | 4/10 | N+1 ×4, indekslar yo'q, kesh yo'q, `CONN_MAX_AGE` yo'q |
| Ma'lumot yaxlitligi | 3/10 | 7/7 sinov teshik topdi |
| Cross-service xavfsizlik | 3/10 | `auto_now_add` tuzog'i, kontrakt testi yo'q |
| Test qamrovi | 0/10 | Nol |
| Infratuzilma | 1/10 | CI, Docker, README, linter — hech biri yo'q |
| i18n content modeli | 9/10 | Uch til, toza fieldsets |

**Xulosa:** frontend bilan bir xil naqsh — **kod yozilishi yaxshi, atrofidagi
muhandislik intizomi yo'q**. Model va admin qatlami tajribali qo'l ishi
(`db_table`, `PROTECT`, `readonly_fields`, `autocomplete_fields` — bular
tasodifan chiqmaydi). Lekin production'ga chiqarish uchun kerak bo'lgan
qatlam — xavfsiz konfiguratsiya, DB constraint'lari, indekslar, kesh, test,
CI, Docker — deyarli butunlay yo'q.

Eng xavflisi **xavfsizlik default'lari zanjiri**: `.env.example` yo'qligi
sababli birinchi deploy `dev-secret-key` + `DEBUG=True` bilan ketishi juda
ehtimol, va buni hech narsa to'xtatmaydi.

P0 + P1 ro'yxati (~3-4 kun) uni haqiqiy production darajaga olib chiqadi.
