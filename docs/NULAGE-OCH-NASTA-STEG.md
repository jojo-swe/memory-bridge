# Memory Bridge – nuläge och nästa arbetspass

Sparat 2026-10-26. Utgångspunkt: version 0.11.0 på branchen
`feature/swap-experiment-block-export` (PR mot `main`).

## Slutsats

Beslutsgrinden från 0.10 är nu prövad med verkligt kernel-swap under
kontrollerad minnesbrist. **Nyttan uteblev**: med lokal swap på lagring som
läser 4 KiB på 50–75 µs (SSD- eller värdcache-klass) är Memory Bridge 5,7×
långsammare än vanlig lokal swap och 1,75× långsammare än samma kodväg utan
givare, med icke överlappande intervall över upprepningar och körordningar. Korrekthet och feltolerans höll: 0 verifieringsfel i alla
körningar, fallback inom en I/O-deadline vid fryst/död givare och tappad
anslutning, ingen tyst återanslutning. Detaljer och rådata:
[SWAP-RESULTAT.md](SWAP-RESULTAT.md), kriterier: [SWAP-EXPERIMENT.md](SWAP-EXPERIMENT.md).

Med emulerad sömn på +1 ms / +5 ms per lokal I/O var bridge 1,6× / 2,4×
snabbare än samma Python/FUSE-väg utan givare (n = 2, loopback, inte jämfört
med kernel-swap på vanlig fil). Tolkning: fjärrcachen kan löna sig först när
en lokal 4 KiB-slumpläsning kostar mer än ≈ 0,3–0,4 ms (HDD, SD-kort, eMMC),
vilket är en smalare målgrupp än "vanliga konsumenter med för lite RAM"; över
ett riktigt LAN blir fjärrvägen dessutom dyrare än på loopback. Detta är inte
mätt på riktig långsam lagring.

## Vad som finns och har verifierats i 0.11

- `block-export`: låntagarens lager som FUSE-fil, `direct_io`, `mlockall`,
  `EIO` vid lagerfel, `stats.json`; loop+`mkswap`+`swapon` ovanpå fungerar.
- `DirectFileStorage` (O_DIRECT) så att sidcachen inte döljer diskkostnad.
- Write-behind-spegling i `ResilientStorage` med ACK-gated giltighet, budget,
  och lokala läsningar medan en batch sänds (−16 % i random-fasen).
- `pressure-workload` (sidverifierad), `swap-lab` (disponibel VM, cgroup v1,
  roterad ordning, felinjektion, städning) och `swap-report` (tabeller).
- Klientomstart efter SIGKILL på beständig bakända, via FUSE-filen.
- Startkontroller respekterar `CAP_IPC_LOCK`; testsviten oberoende av root.
- 177 tester utan överhoppade i miljön med NBD- och FUSE-beroenden.

Inte verifierat: fysiskt LAN, global minnesbrist (deadlockrisk för Python i
swapvägen), strömavbrott, omstart av daemonen under aktivt swap.

## Kostnadsbild att utgå från

Per cachad 4 KiB-läsning på loopback (`docs/experiments/microbench-latency.txt`):
nbdkit+TLS ≈ 100 µs (golv även med nbdkits C-plugin), projektets Python-plugin
+30 µs, processhoppet till NBD-arbetaren +100 µs (ett rent socketpair-eko
mellan två Python-processer kostar 76 µs), SHA-256 och lagerlogik +5–70 µs
beroende på körning. Diskläsning: ≈ 47–56 µs. FUSE+loop+Python kostar dessutom
≈ 3,3× i sig mot kernel-swap på en vanlig fil.

## Vägval

1. **Smalare målgrupp eller annat användningsfall.** Mät på en riktig maskin
   med långsam lagring (HDD/SD/eMMC) över LAN innan mer byggs; den emulerade
   brytpunkten måste bekräftas med fysisk nätverkslatens.
2. **Ta bort processhoppen om man fortsätter.** En kärnklient (`nbd-client`
   eller ublk) på låntagaren med lokal kopia i C, eller en enda process med
   libnbd:s asynkrona API, tar bort ≈ 150–250 µs per läsning. Nätverksvändan
   och TLS återstår; på SSD räcker det ändå inte.
3. **Inte GUI, inte fler plattformar.** Beslutsgrinden ("fortsätt mot
   konsumentprodukt när nyttan visats") är inte passerad.

## Kvarstående konkreta hinder

- Python i swapvägen kan inte göras deadlocksäker under global minnesbrist.
- Givaren behöver 768 MiB låst RAM + ≈ 24 MiB; låntagarsidan ≈ 180 MiB i
  Python-processer utöver full lokal kopia på disk.
- Write-behind-budgeten (16 MiB) blev full under swap-ut-toppar; 12–14 % av
  blockskrivningarna lämnades olagrade. Större budget kostar låst RAM hos
  låntagaren.
- Experimentkärnan saknade NBD-klient och ublk; FUSE+loop är en labbväg.

## Fortsättningsinstruktion

> Läs detta dokument, SWAP-RESULTAT.md och ARKITEKTUR.md. Bygg inte fler
> funktioner ovanpå den nuvarande låntagarvägen förrän en fysisk mätning på
> långsam lagring över LAN visat nytta. Om ett vägbyte görs, behåll
> lokal-first, verifiering före exponering, deadlines och exklusivt ägande.
