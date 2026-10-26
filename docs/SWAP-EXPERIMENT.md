# Swap-experiment i disponibel VM — design och acceptanskriterier

Skrivet 2026-10-26 **innan** några mätresultat fanns. Mätresultaten och
slutsatserna finns separat i [SWAP-RESULTAT.md](SWAP-RESULTAT.md); den här
filen får inte ändras i efterhand för att passa resultaten.

## Frågan

Får ett program som behöver mer anonymt minne än det har tillgång till fler
jobb klara, kortare total tid eller bättre svarstider om dess swap ligger på
Memory Bridge (lokal kopia + fjärr-RAM-cache över TLS/NBD) jämfört med vanlig
lokal swap på samma disk, med samma RAM-budget, samma swapkapacitet och samma
program? Och förblir datan korrekt när givaren fryser, dör, nätverket tappas
eller klienten startas om?

## Miljöinventering (observerad, inte antagen)

| Egenskap | Värde |
| --- | --- |
| Miljö | Disponibel Firecracker-microVM (Anthropics molnsandlåda), inte utvecklarens dator |
| Kärna | Linux 6.18.44, monolitisk (`nomodule`, inga laddbara moduler) |
| CPU/RAM | 2 vCPU, 8 220 244 kB MemTotal |
| Disk | `/dev/vda` virtio-blk, ext4, rapporterar `rotational=1`; värdens cache kan inte styras inifrån VM:en |
| Rättigheter | root, `memlock` obegränsad för root (CAP_IPC_LOCK) |
| Virtualisering inuti | Ingen `/dev/kvm`, ingen nästlad VM |
| Kärnblockenheter | `CONFIG_BLK_DEV_LOOP=y` (8 loop-enheter), `CONFIG_BLK_DEV_NBD` **ej satt**, `CONFIG_BLK_DEV_UBLK` ej satt, ingen device-mapper |
| FUSE | `CONFIG_FUSE_FS=y`, `/dev/fuse`, libfuse 2.9.9 och 3.14 via apt |
| Swap | `CONFIG_SWAP=y`, inget swap aktivt före experimentet |
| cgroup | v1-hierarki med `memory`-controller och `memory.memsw.*` (swapaccounting på), PSI på |
| Nätverk | loopback; `ss -K` (INET_DIAG_DESTROY) tillgängligt för att döda en TCP-anslutning utan brandväggsändring |
| NBD-paket | nbdkit 1.36.3, python3-libnbd (Python 3.12) |

Konsekvens: den kernelnära vägen `nbd-client` finns inte. Blockadaptern måste
exponera låntagarens återhämtningslager på annat sätt. Den enda kernelväg som
finns utan moduler är **FUSE-fil → loop-enhet**. Det är inte den slutliga
produktvägen (ublk eller nbd-client i kärnan vore effektivare) men den räcker
för ett isolerat, reversibelt experiment.

## Designbeslut (reversibla)

1. **Blockadapter**: `memory_bridge/block_export.py` exponerar ett enda
   filobjekt `volume` i ett FUSE-filsystem ovanpå valfritt `Storage`
   (normalt `ResilientStorage(local, remote)`). FUSE monteras med `direct_io`
   så att kärnans sidcache inte lagrar en andra kopia av swapdata, och med
   `big_writes`. Daemonen anropar `mlockall(MCL_CURRENT | MCL_FUTURE)` så att
   dess egna sidor inte kan swappas ut till enheten den själv betjänar.
   Loop-enheten skapas med `losetup --direct-io=on` så att inte heller loopens
   läsningar av FUSE-filen går via sidcache.
2. **Lokal kopia med O_DIRECT**: `memory_bridge/direct_file.py` är en lokal
   bakända som öppnar sin temporära fil med `O_DIRECT`. Utan detta skulle både
   lokal swap och Memory Bridges lokala kopia i praktiken serveras ur värd-RAM
   (sidcachen, utanför den begränsade cgroupen), vilket vore en dold fördel som
   gör jämförelsen meningslös. Basalternativet lokal swap använder därför också
   `losetup --direct-io=on` över en förallokerad vanlig fil på samma disk.
3. **Samma beständighetskrav i alla alternativ**: swap behöver inte överleva
   omstart. Alla alternativ använder temporära filer utan `fsync` per
   skrivning. `fsync`/`flush` från kärnan vidarebefordras till den lokala
   bakändan (`fdatasync`) och ingenting starkare annonseras. Fjärrcachen är en
   cache, aldrig en beständighetsgaranti.
4. **Minnesbrist via cgroup v1**: arbetslasten körs i en egen cgroup med
   `memory.limit_in_bytes = RAM-budget` och `memory.memsw.limit_in_bytes =
   RAM-budget + swapkapacitet`. Det ger verklig kernel-reclaim och verklig
   swap-I/O för arbetslasten, utan att hela VM:en svälter. FUSE-daemonen,
   givaren och NBD-arbetaren ligger **utanför** cgroupen. Det här är inte
   detsamma som fysisk minnesbrist i hela systemet; se risker nedan.
5. **Experimentet rör aldrig vardagsdatorn**: `swap-lab` vägrar köra om det
   finns aktivt swap sedan tidigare, om loop/cgroup/FUSE saknas, eller utan
   flaggan `--disposable-vm`. Allt den skapar (cgroup, loop, swap, mount,
   temporära filer, givare) tas bort före rapport, även vid fel.

## Alternativ som jämförs (samma RAM-budget, disk, program, seed)

| Namn | Swap | Beskrivning |
| --- | --- | --- |
| `noswap` | ingen | Visar att arbetslasten faktiskt inte ryms (förväntad OOM) |
| `local-swap` | loop(direct-io) över vanlig förallokerad fil | Basalternativ |
| `fuse-local` | loop över FUSE över `DirectFileStorage` | Isolerar kostnaden för Python/FUSE-vägen utan givare |
| `bridge` | loop över FUSE över `ResilientStorage(DirectFileStorage, RemoteNBDStorage)` | Memory Bridge med TLS/NBD-givare på loopback |
| `more-ram` | ingen, RAM-budget = RAM + swap | Referens: vad bättre minne skulle ge |

## Arbetslast

`memory_bridge/pressure_workload.py` allokerar `dataset` MiB anonymt minne
(`mmap`) i cgroupen och arbetar i faser med deterministiskt innehåll
(SHAKE-128 av seed, sidindex och version):

1. **fill**: sekventiell skrivning av alla sidor.
2. **sweep**: sekventiell verifiering av alla sidor.
3. **random**: `rounds × pages` slumpmässiga sidåtkomster, var åttonde
   skriver om sidan (ny version) så att smutsiga sidor måste swappas ut igen;
   varje läsning verifieras. Mönster `uniform` eller `hotset` (80 % av
   åtkomsterna mot 20 % av sidorna, närmare verkliga program).

Standardparametrar: RAM-budget 256 MiB, dataset 512 MiB, swap 768 MiB,
2 varv. Datamängden överskrider alltså budgeten med 2×. Programmet
rapporterar per fas: tid, antal åtkomster, latensfördelning (median, p95,
p99, max) per sidåtkomst, antal verifieringsfel och avslutade varv.

## Mätvärden som registreras per körning

- Exakt kommando, paketversioner, kärna, `losetup -l`, `/proc/swaps`.
- Från `/proc/vmstat`: `pswpin`, `pswpout` (delta).
- Från cgroupen: `memory.max_usage_in_bytes`, `memory.memsw.max_usage_in_bytes`,
  `memory.failcnt`, `memory.oom_control` (`oom_kill`), `memory.stat`.
- PSI `memory` (`some`/`full` total) för hela systemet, delta.
- Samtidig minnesåtgång: `VmHWM` och `VmRSS` för arbetslast, FUSE-daemon,
  NBD-arbetare och nbdkit-givare, samt givarens låsta budget.
- Vid injicerade fel: maximal åtkomstlatens (väntetid), tid till fallback,
  `remote_enabled` före/efter, antal verifieringsfel.
- Rådata som JSON per körning.

## Acceptanskriterier (fastställda före mätning)

- **K1 Korrekthet**: 0 verifieringsfel i alla swap-alternativ i alla
  upprepningar; ingen OOM-kill i alternativ där dataset ≤ RAM + swap.
- **K2 Nytta**: `bridge` räknas som bevisat bättre än `local-swap` endast om
  medianen av total tid för fasen `random` är **minst 10 % lägre** och p99 för
  sidåtkomst inte är högre, över minst 3 upprepningar i vardera körordning.
  Om `bridge` är långsammare eller skillnaden ligger inom spridningen: nyttan
  är **inte visad** i denna miljö.
- **K3 Overheadattribution**: skillnaden `fuse-local` − `local-swap` redovisas
  som Python/FUSE-kostnad, skillnaden `bridge` − `fuse-local` som kostnad/vinst
  av fjärrcachen. Ingen av dem får tolkas som den andra.
- **K4 Feltolerans**: vid fryst givare, dödad givare och dödad TCP-anslutning
  under pågående swap ska arbetslasten slutföras med 0 verifieringsfel, den
  längsta väntetiden ska vara ≤ `io-timeout` + 1 s, och lagret ska därefter
  rapportera `remote_enabled=false` permanent (ingen tyst återanslutning).
- **K5 Klientomstart**: data skrivna via blockexporten med beständig bakända
  ska läsas tillbaka korrekt i en ny daemonprocess efter SIGKILL. Omstart av
  en FUSE-daemon under *aktivt swap* förväntas inte vara säker; det mäts
  bara om allt annat är klart och redovisas då som en observation.
- **K6 Minnesärlighet**: rapporten måste visa att låntagarsidan fortfarande
  behöver full lokal kopia på disk, och ange faktisk samtidig RSS för
  bridge-processerna. "Sparat RAM" påstås aldrig utifrån detta experiment.

## Kända risker som granskas i koden

- **Rekursiv swap / deadlock**: FUSE-daemonens sidor får inte ligga i
  swapbar minnesbudget (mlockall, egen cgroup). Python allokerar minne i
  I/O-vägen (bytes-objekt, SHA-256). Under *global* minnesbrist kan kärnan
  vänta på att daemonen allokerar, som i sin tur väntar på kärnan. Det kan
  inte lösas i Python; därför begränsas trycket till arbetslastens cgroup och
  labbet vägrar köra utan sådan avgränsning. Detta är en känd begränsning för
  en konsumentprodukt och måste lösas i en kärn- eller C-adapter.
- **Flush-garantier**: `fsync` på FUSE-filen → `fdatasync` på lokal fil.
  Loop med direct-io skickar `REQ_PREFLUSH`/`FUA`-liknande anrop som
  `fsync`. Fjärrcachen deltar inte i någon garanti.
- **Osäkra skrivningar**: oförändrat från `ResilientStorage`: lokal skrivning
  först; fjärrfel kopplar bort cachen permanent; lokalt fel spärrar lagret
  och FUSE returnerar `EIO`, vilket kärnan behandlar som swapfel.
- **Exklusivt ägande**: loop-enheten pekar på en fil som bara daemonen äger;
  givaren tillåter en session; ingen reconnect.

## Vad detta experiment inte visar

- Fysisk LAN-latens (allt går på loopback).
- Prestanda på en långsam SSD/HDD; `/dev/vda` kan vara värdcachad.
- Global minnesbrist i hela systemet (cgroup-begränsning används).
- Beständighet vid strömavbrott.
- Att vanliga program "får mer RAM": låntagaren behöver fortfarande disk för
  hela kopian och RAM för sina egna processer.
