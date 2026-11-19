# AGY Token Monitor

Antigravity Desktop oturumlarındaki her turun ve oturumun toplam token harcamasını ekran köşesinde küçük, her zaman üstte (always-on-top) bir pencerede gösterir ve tüm istatistikleri yerel SQLite veritabanına (`data/history.db`) kaydeder.

## Çalıştırma

```zsh
cd /Users/k/.gemini/antigravity/scratch/agy-token-monitor
./run.sh
```

## Özellikler
- **Ekran Köşesi HUD:** Sağ alt köşede açılır, sürüklenebilir, `📌` ile her zaman üstte tutulabilir veya daraltılabilir (`▾`).
- **Oturum Bazlı Takip:** `Oto` açıkken en son güncellenen oturumu otomatik izler; açılır listeden veya `ID Gir` butonundan istediğiniz Conversation ID'ye geçebilir, `✎` ile oturuma isim verebilirsiniz.
- **Son Tur & Oturum Toplamı:** Her turun `input_tokens`, `cache_read_tokens`, `output_tokens`, adım/tool sayısı, süresi ve oturum toplamını gösterir.
- **Kalıcı Geçmiş & İstatistikler:** `data/history.db` içinde `sessions`, `turns` ve `steps` tablolarında tüm alanlar (tool çağrıları, düşünme/içerik karakter uzunlukları, hata sayıları, zaman damgaları) saklanır ve CSV olarak dışa aktarılabilir.
