# Memory Bridge – nuläge och nästa arbetspass

Sparat 2026-10-25. Utgångspunkt: version 0.10.0 på `main`.

## Slutsats

Vi har ett fungerande Linux-ramverk för fjärrlagring i RAM, med lokal reservkopia
 och verifierade felvägar. Vi har ännu inte visat att lösningen hjälper vanliga
program på en dator med för lite RAM. Den viktigaste återstående frågan är
mätbar användarnytta, före fler funktioner och ett konsumentgränssnitt.

## Vad som finns och har verifierats

- Givar-RAM med fast budget, obligatorisk minneslåsning och kvoter.
- TLS/NBD, privat delad nyckel och en autentiserad session per givarstart.
- Lokal skrivgenomgående kopia; temporär fil eller valfri beständig SQLite-bakända.
- Tidsgränser för fjärranrop och permanent fallback till lokal kopia vid givarfel.
- Lokala SHA-256-digester som verifierar hela cacheblock före användning.
- Atomiska lokala skrivningar och testad återöppning efter klientkrasch.
- Kompakt metadata, större sammanhängande överföringar och jämförande benchmark.
- Installerbara CLI-kommandon som fungerar utan repots rotskript.

Senaste verifierade CI: **149 godkända tester, inga överhoppade**.
Se [CI-körningen](https://github.com/jojo-swe/memory-bridge/actions/runs/40195407580)
och [testrapporten](TESTRESULTAT.md). Testerna omfattar loopback med riktig
TLS/NBD, död/fryst givare, korrupta cachesvar, processkrasch och paketets CLI.

Dessa tester bevisar de testade funktionerna. De bevisar inte RAM-besparing,
appacceleration, säker system-swap eller beständighet vid fysiskt strömavbrott.

## Resultat som styr nästa beslut

De små jämförelserna hittills gav långsammare fjärrläge än en lokal fil med
varm filcache. Det är inte ett representativt test av en dator under RAM-brist.
Rådata finns i [0.6-rapporten](benchmark-ci-0.6.0.json) och
[0.7-rapporten](benchmark-ci-0.7.0.json). De avser äldre versioner och ska
inte presenteras som prestanda för den nuvarande integritetskontrollen.

Full lokal kopia krävs fortfarande. OS-filcache, givaren, Python-processer,
TLS och kontrollsummor har minneskostnader. Processernas separata RSS-toppar
får inte summeras som samtidiga toppar. RLIMIT_AS-tester begränsar virtuell
adressrymd per process och motsvarar inte systemets fysiska minnestryck.

## Prioritering

1. **Linux-systemintegration i isolerad VM.** Gör lagret tillgängligt för
   swap och testa under kontrollerad minnesbrist. Kartlägg först vad som
   krävs i låntagarens blockadapter: befintlig kod exponerar inte automatiskt
   återhämtningslagret som en kernel-enhet. Testa uppkoppling, fryst/död givare,
   avbrutet nätverk, klientomstart och återställning. Ändra inte vardagsdatorns
   swap som första experiment.
2. **Mät användarnytta med samma arbetslast.** Jämför vanlig lokal swap med
   Memory Bridge under samma RAM-budget, lagring och programversion. Börja
   med en reproducerbar arbetslast som faktiskt överskrider tilldelat RAM.
   Använd först loopback för funktion, därefter två fysiska datorer för LAN.
3. **Säker återlämning av givarminne.** Givaren måste kunna begära tillbaka
   RAM utan att kasta bort aktiva låntagardata. Enbart TTL eller att stänga
   sessionen räcker inte; det behövs ett testat avvecklingsprotokoll.
4. **Enkel installation och parkoppling.** Välj dela/låna, ange budget och
   visa status. Lägg därefter till tjänstestart, uppdateringar och ett enkelt
   gränssnitt. Välj fler plattformar först när nyttan är visad.
5. **Konfidentialitet hos givaren.** TLS skyddar transporten och digester
   upptäcker fel innehåll, men givarens administratör kan läsa klartext.
   Klientkryptering med genomtänkt nyckelhantering behövs för mindre betrodda
   givare. Detta är separat från den befintliga integritetskontrollen.

## Konkret startpunkt nästa gång

Börja med en inventering av VM-möjligheter och behörigheter. Skapa därefter
ett dokumenterat VM-experiment med fasta resurser och snapshots. NAS:en var
inte tillgänglig under tidigare utveckling; förutsätt inte att den är online.

Innan ny implementation: identifiera hur låntagarens återhämtningslager ska
exponeras som blockenhet och vilka timeout-/flush-/felgarantier systemet behöver.
Använd inte den råa engångsgivaren som om den redan hade lokal fallback.

Registrera åtminstone:

- faktisk RAM- och swapbudget, lagringstyp och nätverkslatens;
- arbetslast, seed, versioner, upprepningar och körordning;
- slutförda jobb, total tid, svarstider, swap-I/O och eventuella OOM-fel;
- samtidig minnesåtgång på låntagare och givare samt filcache;
- korrekthet och väntetid vid varje injicerat avbrott.

Ändra inte databevarande eller beständighetskrav mellan alternativen utan att
redovisa det. Nuvarande benchmark med temporär fil har andra skrivkostnader
än den beständiga SQLite-bakändan.

## Beslutsgrind

Fortsätt mot konsumentprodukt när en representativ arbetslast visar att
Memory Bridge slutför mer arbete eller förbättrar användbara svarstider under
RAM-brist, med acceptabel overhead och utan datafel i avbrottstesterna.
Bestäm kvantitativa gränser innan mätningarna körs.

Om vinsten uteblir: analysera var kostnaden ligger och pröva en ändrad
arkitektur eller smalare målgrupp. Lägg inte ett stort GUI ovanpå en lösning
vars nytta fortfarande är obevisad. En återgång eller ett vägbyte ska bygga
på mätdata, inte på antalet godkända enhetstester.

## Fortsättningsinstruktion

> Läs detta dokument, ARKITEKTUR.md och TESTRESULTAT.md. Kontrollera senaste
> main och behåll redan verifierade garantier. Prioritera isolerad
> systemintegration och reproducerbar nytta under minnesbrist. Dokumentera
> vad som faktiskt körts och skilj det från antaganden och framtida mål.
