# Swap-experiment 0.11 – förregistrerade acceptanskriterier

Skrivet och committat **innan** några jämförande mätningar mellan
konfigurationerna körts. Ändringar efter mätstart redovisas i
[SWAP-EXPERIMENT-RESULTAT.md](SWAP-EXPERIMENT-RESULTAT.md) med motivering;
tröskelvärdena nedan ändras inte i efterhand.

## Fråga

Slutför en Linux-dator med för lite RAM mer arbete, eller får den bättre
svarstider, när swap ligger på Memory Bridge (lokal kopia + verifierad
fjärrcache i en givares RAM) jämfört med vanlig lokal swap på samma disk?

## Miljö och avgränsning

- Disponibel Linux-VM: sessionens egen Firecracker-mikro-VM (Ubuntu 24.04,
  2 vCPU, 8 GiB). Den skapas för sessionen, innehåller inga användardata och
  kastas efteråt. Ingen användardator, NAS eller värdmaskin berörs.
- Kärnan saknar NBD- och ublk-stöd och /dev/kvm saknas (ingen nästlad VM med
  KVM). Blockenheten exponeras därför som `loop` → FUSE-fil (`nbdfuse`) → NBD
  över Unix-socket → låntagaradaptern. På en vanlig kärna ersätts de två
  första leden av `nbd-client`; den vägen körs **inte** här.
- Minnesbrist skapas med en cgroup v1-gräns (`memory.limit_in_bytes`) för
  arbetslasten. Det är verklig fysisk återvinning och swap-ut av arbetslastens
  sidor, inte RLIMIT_AS. Swap är systemglobal men bara arbetslastens sidor
  pressas ut, eftersom resten av VM:en har gott om ledigt RAM.
- Givaren körs i samma VM på loopback-TCP med TLS, utanför arbetslastens
  cgroup. Dess låsta RAM räknas som "en annan dators" minne och redovisas
  separat. Nätprofiler: loopback (bästa fall för fjärrläget) och 1 ms
  fördröjning per begäran via nbdkit:s delay-filter (grov modell av långsamt
  LAN/Wi-Fi).

### Känd snedvridning som redovisas, inte döljs

I huvudserien körs adapter, nbdfuse och NBD-worker **utanför** arbetslastens
cgroup. De står alltså inte under samma minnestryck som arbetslasten. På en
riktig dator med global RAM-brist gör de det, med risk för rekursiv swap och
deadlock. Det ger Memory Bridge en robusthetsfördel i huvudserien. Separat
stressvariant (S nedan) placerar dem i samma cgroup. Adapterprocesserna,
givaren och CPU-tid per komponent mäts i alla körningar. De delar också de
2 vCPU:erna med arbetslasten, vilket i stället missgynnar fjärrläget jämfört
med två fysiska datorer.

## Konfigurationer (samma arbetslast, seed och cgroup-gräns)

| Kod | Beskrivning |
| --- | --- |
| N | 256 MiB, ingen swap |
| L | 256 MiB, 1 GiB lokal swapfil på samma ext4 (/dev/vda) |
| P | 256 MiB, 1 GiB swap via adaptern, **endast lokal kopia** (O_DIRECT) – mäter kostnaden för användarrymdsvägen |
| R | 256 MiB, 1 GiB swap via adaptern, lokal kopia + fjärrcache (TLS/NBD, loopback) |
| R1 | som R men 1 ms fördröjning per givarbegäran |
| Ref | 1024 MiB, ingen swap – referens "mer RAM" |

Alla swap-varianter använder samma swappiness (60), samma disk för lokal data
och ingen sidcache för swapdata: L går direkt mot blockenheten via swapfil;
P/R öppnar lokal kopia med O_DIRECT och loop-enheten med `--direct-io=on`.
Ingen komprimering (zswap/zram avstängt) i någon variant.

## Arbetslaster (fastställda före mätning)

- **W1 – xz:** `xz -8 -T1` på 48 MiB deterministisk, delvis komprimerbar
  indata (behöver ca 370 MiB > 256 MiB). Mått: total jobbtid, slutfört inom
  900 s, OOM, SHA-256 av utdata mot referens.
- **W2 – pagestress:** självverifierande C-program, 512 MiB data (2× budget),
  heta 20 % av sidorna får 80 % av åtkomsterna, 8 sidor per begäran, 10 %
  skrivningar, 60 s begärandefas. Mått: slutförda begäranden, svarstid
  p50/p99/p99.9/max, populeringstid, verifieringsfel, slutsvep.

Kalibrering av arbetslastens storlek får ske före huvudserien om ett jobb
inte är genomförbart inom tidsgränsen; det görs med Ref/N och enstaka
röktester, redovisas och påverkar inte trösklarna.

## Upprepning och ordning

Minst 5 omgångar för N, L, P, R och Ref per arbetslast, minst 3 för R1.
Inom varje omgång körs konfigurationerna i en seedad slumpordning som sparas.
Sidcache töms (`drop_caches`) och swap stängs av mellan körningar. Rapportera
median och min–max; inga körningar utesluts utan redovisad orsak.

## Mätvärden per körning

Exakta kommandon, versioner, cgroup-gräns, swapstorlek, nätprofil; slutstatus,
total tid, svarstider; `pswpin/pswpout` (vmstat-delta), cgroup failcnt och
OOM-kills, PSI; samtidiga minnessampel (≈4 Hz) av arbetslastens cgroup,
adapter, nbdfuse, NBD-worker, givare samt systemets fria minne/sidcache;
CPU-tid per komponent; adapterns statistik (fjärrträffar, fel, fallback).

## Validitetsgrindar (annars är serien ogiltig och kalibreras om)

1. N misslyckas (OOM eller ej slutfört) för båda arbetslasterna.
2. Ref slutförs utan fel för båda.
3. L slutförs utan datafel i alla omgångar.

## Beslutskriterier för användarnytta (R mot L)

**Nytta visad** om alla gäller:

1. W1: median jobbtid minst 10 % lägre för R, **eller** W2: median antal
   slutförda begäranden minst 10 % högre för R med p99 högst 10 % sämre.
2. Riktningen gäller i minst 4 av 5 parade omgångar för det kriteriet.
3. Noll datafel och inga fler OOM/misslyckade jobb än L.
4. Samtidig topp-RSS för adapter + worker + nbdfuse ≤ 64 MiB (25 % av budgeten).

Om 1–4 bara uppfylls på loopback men inte i R1 redovisas nyttan som
villkorad av ett idealiserat nät. Uppfylls de inte alls: **nytta uteblev**
i denna miljö. Sekundäranalys: P mot L (kostnad för vägen) och R mot P
(fjärrcachens effekt), utan beslutsstatus.

## Felinjektion (R, W2, 60 s, fel vid ca 20 s in i begärandefasen)

Minst 3 upprepningar per fel. Fjärr-timeout 2 s.

| Fel | Godkänt |
| --- | --- |
| F1 fryst givare (SIGSTOP) | jobbet slutförs, 0 datafel, fallback syns i adapterstatistik, max svarstid ≤ 4 s |
| F2 död givare (SIGKILL) | samma |
| F3 tappat nätverk (nftables drop mot givarporten) | samma |
| F4 låntagaradaptern dödas (SIGKILL) | ingen tyst korruption: jobbet misslyckas synligt **eller** slutförs med 0 fel; utfallet redovisas |
| F4b adapteromstart | ny adapterprocess över samma lokala kopia läser 100 % av kvitterade skrivningar korrekt |

## Stressvariant S (säkerhet, inget nyttokriterium)

Som R men adapter, worker och nbdfuse i arbetslastens cgroup. Watchdog 300 s.
Redovisa om systemet blir klart, hänger (deadlock) eller ger OOM i I/O-vägen.

## Vad som inte kan visas här

Fysiskt LAN, kärnans `nbd`-modul, global RAM-brist för hela datorn,
strömavbrott, andra operativsystem och verkliga konsumentprogram utöver W1.
