# Memory Bridge – nuläge och nästa arbetspass

Sparat 2026-10-26. Utgångspunkt: version 0.11.0 på branchen
`feature/swap-experiment-block-export` (PR mot `main`).

## Slutsats

Beslutsgrinden från 0.10 är nu prövad med verkligt kernel-swap under
kontrollerad minnesbrist. **Nyttan uteblev**: med lokal swap på SSD-klassad
lagring är Memory Bridge 5,7× långsammare än vanlig lokal swap och 1,75×
långsammare än samma kodväg utan givare, med liten spridning över upprepningar
och körordningar. Korrekthet och feltolerans höll: 0 verifieringsfel i alla
körningar, fallback inom en I/O-deadline vid fryst/död givare och tappad
anslutning, ingen tyst återanslutning. Detaljer och rådata:
[SWAP-RESULTAT.md](SWAP-RESULTAT.md), kriterier: [SWAP-EXPERIMENT.md](SWAP-EXPERIMENT.md).

Fjärrcachen lönar sig först när en lokal 4 KiB-slumpläsning kostar mer än
≈ 0,4 ms (HDD, SD-kort, eMMC); då var bridge 1,6–2,4× snabbare än samma väg
utan givare i emulerade körningar. Det är en smalare målgrupp än "vanliga
konsumenter med för lite RAM", och över ett riktigt LAN blir fjärrvägen bara
dyrare än på loopback.

## Vad som finns och har verifierats i 0.11

- `block-export`: låntagarens lager som FUSE-fil, `direct_io`, `mlockall`,
  `EIO` vid lagerfel, `stats.json`; loop+`mkswap`+`swapon` ovanpå fungerar.
- `DirectFileStorage` (O_DIRECT) så att sidcachen inte döljer diskkostnad.
- Write-behind-spegling i `ResilientStorage` med ACK-gated giltighet, budget,
  och lokala läsningar medan en batch sänds (−16 % total tid).
- `pressure-workload` (sidverifierad), `swap-lab` (disponibel VM, cgroup v1,
  roterad ordning, felinjektion, städning) och `swap-report` (tabeller).
- Klientomstart efter SIGKILL på beständig bakända, via FUSE-filen.
- Startkontroller respekterar `CAP_IPC_LOCK`; testsviten oberoende av root.
- 177 tester utan överhoppade i miljön med NBD- och FUSE-beroenden.

Inte verifierat: fysiskt LAN, global minnesbrist (deadlockrisk för Python i
swapvägen), strömavbrott, omstart av daemonen under aktivt swap.

## Kostnadsbild att utgå från

Per cachad 4 KiB-läsning på loopback: nbdkit+TLS ≈ 100 µs (golv även med
nbdkits C-plugin), projektets Python-plugin +22 µs, processhoppet till
NBD-arbetaren +150 µs (varav ≈ 87 µs är ren schemaläggning av två
Python-processer), SHA-256 och lagerlogik +70 µs. Diskläsning: ≈ 47 µs.
FUSE+loop+Python kostar dessutom ≈ 3,3× i sig mot kernel-swap på en vanlig fil.

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
- Givaren behöver 768 MiB låst RAM + ≈ 50 MiB; låntagarsidan ≈ 180 MiB i
  Python-processer utöver full lokal kopia på disk.
- Write-behind-budgeten (16 MiB) blev full under swap-ut-toppar; 30 % av
  blocken lämnades olagrade. Större budget kostar låst RAM hos låntagaren.
- Experimentkärnan saknade NBD-klient och ublk; FUSE+loop är en labbväg.

## Fortsättningsinstruktion

> Läs detta dokument, SWAP-RESULTAT.md och ARKITEKTUR.md. Bygg inte fler
> funktioner ovanpå den nuvarande låntagarvägen förrän en fysisk mätning på
> långsam lagring över LAN visat nytta. Om ett vägbyte görs, behåll
> lokal-first, verifiering före exponering, deadlines och exklusivt ägande.
