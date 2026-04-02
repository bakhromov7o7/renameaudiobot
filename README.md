# Rename Audio Bot

Python va Telegram uchun yozilgan bot quyidagi ketma-ketlikda ishlaydi:

1. Foydalanuvchi rasm yuboradi.
2. Bot rasmni vaqtincha `storage/tmp/<user_id>/` ichiga saqlaydi va PostgreSQL ga yozadi.
3. Foydalanuvchi audio nomini yuboradi.
4. Foydalanuvchi ijrochi nomini yuboradi.
5. Bot nom va ijrochini PostgreSQL ga yozadi.
6. Foydalanuvchi audio yuboradi.
7. Bot audio faylni yuklab oladi, cover, title va artist ni yangilaydi, fayl nomini ham yangi nom bilan yuboradi.
8. Tayyorlangan fayl foydalanuvchiga qaytariladi.
9. Vaqtinchalik fayllar va DB yozuvi o'chiriladi.

## Qo'llab-quvvatlanadigan formatlar

- `mp3`
- `m4a`
- `mp4`
- `flac`
- `wav`
- `ogg`
- `opus`
- `webm`

Bot original audio formatini saqlaydi. Agar format metadata cover yozishni qo'llamasa yoki fayl shikastlangan bo'lsa, foydalanuvchiga tushunarli xabar beradi va sessiyani tozalaydi.

## O'rnatish

1. Virtual muhit yarating:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. `ffmpeg` o'rnating:

```bash
brew install ffmpeg
```

3. PostgreSQL da database tayyorlang:

```sql
CREATE DATABASE renameaudiobot;
```

4. `.env.example` dan nusxa olib `.env` yarating va qiymatlarni to'ldiring.

5. Botni ishga tushiring:

```bash
python3 main.py
```

## Muhim eslatmalar

- Jadvalni bot o'zi yaratadi.
- Format aniqlashda fayl kengaytmasiga emas, audio konteynerning o'ziga qaraladi. Shu sabab noto'g'ri kengaytmali audio ham ko'p holatda to'g'ri qayta ishlanadi.
- Fayllar faqat `STORAGE_DIR` ichida saqlanadi.
- Telegram Bot API cheklovi sabab bot katta fayllarni yuklab ola olmasligi mumkin. Shu loyiha default holatda `20 MB` gacha tekshiradi.
- `/start` yangi sessiya boshlaydi va eski vaqtinchalik fayllarni tozalaydi.
- `/cancel` joriy jarayonni bekor qiladi va tizimni tozalaydi.
