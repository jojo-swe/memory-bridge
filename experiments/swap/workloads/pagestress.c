/*
 * pagestress.c - self-verifying anonymous-memory workload for swap experiments.
 *
 * Purpose
 * -------
 * Allocate a large anonymous mapping (typically larger than the cgroup's
 * memory.max so the kernel must swap), fill every 4 KiB page with
 * deterministic pseudo-random content, then run a skewed (hot/cold) random
 * access workload that verifies each touched page against an expected hash
 * and occasionally rewrites it.  Any corruption introduced by the swap path
 * (swap device, network block device, compression, ...) shows up as a hash
 * mismatch.  A final sweep verifies every page once more.
 *
 * Core invariant
 * --------------
 * For every page i at all times outside of write_page(i, ...):
 *
 *     hash_page(buf + i*4096) == expected_hash[i]
 *
 * and the page content equals gen(seed, i, version[i]).  expected_hash[] and
 * version[] are only ever updated by write_page(), immediately after it has
 * written the new content, so the invariant can only be broken by something
 * outside this program's normal write path (i.e. the memory/swap subsystem,
 * or the deliberate --corrupt-for-self-test injection).
 *
 * Page layout (4096 bytes = 512 little-endian uint64 words):
 *     word 0      page index
 *     word 1      version
 *     word 2..511 xorshift64* stream seeded from mix(seed, index, version)
 *
 * Hash
 * ----
 * A cheap word-wise mixing hash: h = f(h ^ w) per word, where
 * f(x) = xorshift(x * odd_constant).  f is a bijection on 64-bit values, so
 * for a fixed sequence of other words, changing any single word always
 * changes the final hash: every single-word (and thus single-byte/bit)
 * corruption is detected with certainty.  Multi-word corruption escapes
 * detection with probability about 2^-64.  It is NOT cryptographic, which
 * is fine: the adversary is hardware/software bugs, not an attacker.
 *
 * Workload metadata (expected_hash: 8 B/page, version: 4 B/page = 12 B/page,
 * plus 4 B per hot page for the hot-set index list, plus the latency array)
 * lives in ordinary heap memory and therefore counts against the same cgroup
 * as the data buffer.  It is small (~0.3% of the buffer) but it is part of
 * the workload's own RAM and may itself be swapped.
 *
 * Build:  gcc -O2 -Wall -Wextra -Werror -std=c11 -D_GNU_SOURCE \
 *             -o pagestress pagestress.c
 *
 * Exit codes: 0 ok, 1 system/allocation error, 2 usage error,
 *             3 data verification error(s) detected.
 */

#include <errno.h>
#include <getopt.h>
#include <inttypes.h>
#include <math.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/resource.h>
#include <time.h>
#include <unistd.h>

#define PAGE_SIZE_REQUIRED 4096u
#define WORDS_PER_PAGE (PAGE_SIZE_REQUIRED / sizeof(uint64_t))
#define LATENCY_CAP 5000000ull
#define PROGRESS_INTERVAL_NS (5ull * 1000000000ull)
/* page_count must fit in uint32 (hot-set indices and version[] are 32-bit). */
#define MAX_SIZE_MIB ((uint64_t)UINT32_MAX / 256u)
#define MAX_REPORTED_MISMATCHES 10

#define EXIT_OK 0
#define EXIT_SYS 1
#define EXIT_USAGE 2
#define EXIT_VERIFY 3

static volatile sig_atomic_t g_stop = 0;

static void on_signal(int sig)
{
    (void)sig;
    g_stop = 1; /* async-signal-safe: only set a flag */
}

/* ------------------------------------------------------------------ PRNG */

static inline uint64_t splitmix64(uint64_t *state)
{
    uint64_t z = (*state += 0x9E3779B97F4A7C15ull);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ull;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBull;
    return z ^ (z >> 31);
}

/* xorshift64*: state must never be 0. */
static inline uint64_t xorshift64s(uint64_t *state)
{
    uint64_t x = *state;
    x ^= x >> 12;
    x ^= x << 25;
    x ^= x >> 27;
    *state = x;
    return x * 0x2545F4914F6CDD1Dull;
}

/* Uniform integer in [0, n), n >= 1, without modulo bias (rejection). */
static inline uint64_t rand_below(uint64_t *state, uint64_t n)
{
    uint64_t threshold = (0 - n) % n; /* = 2^64 mod n */
    for (;;) {
        uint64_t r = xorshift64s(state);
        if (r >= threshold)
            return r % n;
    }
}

/* Uniform double in [0, 1) from the top 53 bits. */
static inline double rand_unit(uint64_t *state)
{
    return (double)(xorshift64s(state) >> 11) * (1.0 / 9007199254740992.0);
}

/* ------------------------------------------------------- page content */

static inline uint64_t content_seed(uint64_t seed, uint64_t index, uint64_t version)
{
    uint64_t s = seed ^ (index * 0xD1B54A32D192ED03ull) ^ (version * 0x8CB92BA72F3D8DD7ull);
    uint64_t z = splitmix64(&s);
    return z ? z : 0x1ull; /* xorshift state must be non-zero */
}

static inline uint64_t hash_page(const uint64_t *p)
{
    uint64_t h = 0xCBF29CE484222325ull;
    for (size_t i = 0; i < WORDS_PER_PAGE; i++) {
        h ^= p[i];
        h *= 0x9E3779B97F4A7C15ull; /* odd multiplier: bijective */
        h ^= h >> 31;               /* xorshift: bijective */
    }
    return h;
}

/* The only routine that changes page content or its metadata. */
static void write_page(uint64_t *page, uint64_t index, uint32_t version, uint64_t seed,
                       uint64_t *expected_hash, uint32_t *versions)
{
    uint64_t st = content_seed(seed, index, version);
    page[0] = index;
    page[1] = version;
    for (size_t i = 2; i < WORDS_PER_PAGE; i++)
        page[i] = xorshift64s(&st);
    expected_hash[index] = hash_page(page);
    versions[index] = version;
}

/* ------------------------------------------------------------- timing */

static inline uint64_t now_ns(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

static int cmp_u64(const void *a, const void *b)
{
    uint64_t x = *(const uint64_t *)a, y = *(const uint64_t *)b;
    return (x > y) - (x < y);
}

/* Nearest-rank percentile on a sorted array: rank = ceil(p/100 * n). */
static uint64_t percentile_nr(const uint64_t *sorted, uint64_t n, double p)
{
    if (n == 0)
        return 0;
    /* Manual ceil avoids a libm dependency (no -lm needed). */
    double x = p / 100.0 * (double)n;
    uint64_t rank = (uint64_t)x;
    if ((double)rank < x)
        rank++;
    if (rank < 1)
        rank = 1;
    if (rank > n)
        rank = n;
    return sorted[rank - 1];
}

/* ---------------------------------------------------------------- CLI */

struct params {
    uint64_t size_mib;
    uint64_t requests;
    double duration_s;
    uint64_t pages_per_request;
    double hot_fraction;
    double hot_probability;
    double write_fraction;
    uint64_t seed;
    const char *json_out;
    bool corrupt_self_test;
    bool print_digest;
};

static void usage(FILE *f)
{
    fprintf(f,
        "usage: pagestress --size-mib N [--requests R] [--duration-s S]\n"
        "                  [--pages-per-request K] [--hot-fraction F]\n"
        "                  [--hot-probability P] [--write-fraction W] [--seed S]\n"
        "                  [--json-out PATH] [--corrupt-for-self-test]\n"
        "  At least one of --requests / --duration-s must be > 0.\n"
        "  Exit: 0 ok, 1 system error, 2 usage error, 3 verification error.\n");
}

static bool parse_u64(const char *s, uint64_t *out)
{
    if (!s || !*s || *s == '-' || *s == '+')
        return false;
    char *end = NULL;
    errno = 0;
    unsigned long long v = strtoull(s, &end, 0);
    if (errno || *end != '\0')
        return false;
    *out = (uint64_t)v;
    return true;
}

static bool parse_double(const char *s, double *out)
{
    if (!s || !*s)
        return false;
    char *end = NULL;
    errno = 0;
    double v = strtod(s, &end);
    if (errno || *end != '\0' || !isfinite(v))
        return false;
    *out = v;
    return true;
}

static bool parse_fraction(const char *s, double *out)
{
    return parse_double(s, out) && *out >= 0.0 && *out <= 1.0;
}

static void json_string(FILE *f, const char *s)
{
    if (!s) {
        fputs("null", f);
        return;
    }
    fputc('"', f);
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        if (*p == '"' || *p == '\\')
            fprintf(f, "\\%c", *p);
        else if (*p < 0x20)
            fprintf(f, "\\u%04x", *p);
        else
            fputc(*p, f);
    }
    fputc('"', f);
}

/* ------------------------------------------------------------- report */

struct results {
    uint64_t page_count, hot_count;
    double populate_s, request_phase_s, final_sweep_s, total_s;
    uint64_t requests_completed;
    uint64_t lat_recorded;
    double p50, p90, p99, p999, lat_max, lat_mean;
    bool latency_truncated, interrupted;
    uint64_t verify_errors, final_sweep_errors;
    uint64_t digest;
    struct rusage ru;
};

static void print_json(FILE *f, const struct params *pp, const struct results *r)
{
    fprintf(f, "{\"tool\":\"pagestress\",\"version\":\"1\",\"params\":{");
    fprintf(f, "\"size_mib\":%" PRIu64 ",\"requests\":%" PRIu64 ",\"duration_s\":%.6f,"
               "\"pages_per_request\":%" PRIu64 ",\"hot_fraction\":%.6f,"
               "\"hot_probability\":%.6f,\"write_fraction\":%.6f,\"seed\":%" PRIu64
               ",\"json_out\":",
            pp->size_mib, pp->requests, pp->duration_s, pp->pages_per_request,
            pp->hot_fraction, pp->hot_probability, pp->write_fraction, pp->seed);
    json_string(f, pp->json_out);
    fprintf(f, ",\"corrupt_for_self_test\":%s}", pp->corrupt_self_test ? "true" : "false");
    fprintf(f, ",\"page_size\":%u,\"page_count\":%" PRIu64 ",\"hot_pages\":%" PRIu64,
            PAGE_SIZE_REQUIRED, r->page_count, r->hot_count);
    fprintf(f, ",\"populate_s\":%.6f,\"request_phase_s\":%.6f", r->populate_s, r->request_phase_s);
    fprintf(f, ",\"requests_completed\":%" PRIu64 ",\"requests_per_s\":%.3f",
            r->requests_completed,
            r->request_phase_s > 0 ? (double)r->requests_completed / r->request_phase_s : 0.0);
    fprintf(f, ",\"latency_ms\":{\"p50\":%.6f,\"p90\":%.6f,\"p99\":%.6f,\"p999\":%.6f,"
               "\"max\":%.6f,\"mean\":%.6f,\"method\":\"nearest-rank\",\"samples\":%" PRIu64 "}",
            r->p50, r->p90, r->p99, r->p999, r->lat_max, r->lat_mean, r->lat_recorded);
    fprintf(f, ",\"latency_truncated\":%s,\"interrupted\":%s",
            r->latency_truncated ? "true" : "false", r->interrupted ? "true" : "false");
    fprintf(f, ",\"verify_errors\":%" PRIu64 ",\"final_sweep_errors\":%" PRIu64,
            r->verify_errors, r->final_sweep_errors);
    fprintf(f, ",\"final_sweep_s\":%.6f,\"total_s\":%.6f", r->final_sweep_s, r->total_s);
    fprintf(f, ",\"max_rss_kib\":%ld,\"major_faults\":%ld,\"minor_faults\":%ld"
               ",\"voluntary_ctx_switches\":%ld,\"involuntary_ctx_switches\":%ld",
            r->ru.ru_maxrss, r->ru.ru_majflt, r->ru.ru_minflt, r->ru.ru_nvcsw, r->ru.ru_nivcsw);
    if (pp->print_digest)
        fprintf(f, ",\"populate_digest\":\"0x%016" PRIx64 "\"", r->digest);
    fprintf(f, "}\n");
}

static void report_mismatch(const char *phase, uint64_t idx, const uint64_t *page,
                            uint64_t expected, uint64_t got, uint32_t version, uint64_t *reported)
{
    if (*reported >= MAX_REPORTED_MISMATCHES)
        return;
    (*reported)++;
    fprintf(stderr,
            "pagestress: %s MISMATCH page=%" PRIu64 " expected_version=%" PRIu32
            " header_index=%" PRIu64 " header_version=%" PRIu64
            " expected_hash=0x%016" PRIx64 " got=0x%016" PRIx64 "\n",
            phase, idx, version, page[0], page[1], expected, got);
}

/* --------------------------------------------------------------- main */

int main(int argc, char **argv)
{
    uint64_t t_start = now_ns();

    /* Install handlers first so an early SIGTERM still yields a report. */
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_signal;
    sigemptyset(&sa.sa_mask);
    if (sigaction(SIGTERM, &sa, NULL) != 0 || sigaction(SIGINT, &sa, NULL) != 0) {
        perror("pagestress: sigaction");
        return EXIT_SYS;
    }

    struct params pp = {
        .size_mib = 0, .requests = 0, .duration_s = 0.0, .pages_per_request = 8,
        .hot_fraction = 0.2, .hot_probability = 0.8, .write_fraction = 0.1,
        .seed = 1, .json_out = NULL, .corrupt_self_test = false, .print_digest = false,
    };
    bool have_size = false;

    enum { O_SIZE = 1000, O_REQ, O_DUR, O_KPR, O_HOTF, O_HOTP, O_WF, O_SEED, O_JSON,
           O_CORRUPT, O_DIGEST, O_HELP };
    static const struct option longopts[] = {
        {"size-mib", required_argument, NULL, O_SIZE},
        {"requests", required_argument, NULL, O_REQ},
        {"duration-s", required_argument, NULL, O_DUR},
        {"pages-per-request", required_argument, NULL, O_KPR},
        {"hot-fraction", required_argument, NULL, O_HOTF},
        {"hot-probability", required_argument, NULL, O_HOTP},
        {"write-fraction", required_argument, NULL, O_WF},
        {"seed", required_argument, NULL, O_SEED},
        {"json-out", required_argument, NULL, O_JSON},
        {"corrupt-for-self-test", no_argument, NULL, O_CORRUPT},
        {"print-digest", no_argument, NULL, O_DIGEST}, /* hidden: for tests */
        {"help", no_argument, NULL, O_HELP},
        {NULL, 0, NULL, 0},
    };

    int c;
    opterr = 1;
    while ((c = getopt_long(argc, argv, "", longopts, NULL)) != -1) {
        bool ok = true;
        switch (c) {
        case O_SIZE: ok = parse_u64(optarg, &pp.size_mib) && pp.size_mib >= 1 &&
                          pp.size_mib <= MAX_SIZE_MIB;
                     have_size = ok; break;
        case O_REQ:  ok = parse_u64(optarg, &pp.requests); break;
        case O_DUR:  ok = parse_double(optarg, &pp.duration_s) && pp.duration_s >= 0.0 &&
                          pp.duration_s < 1e9; break;
        case O_KPR:  ok = parse_u64(optarg, &pp.pages_per_request) &&
                          pp.pages_per_request >= 1 && pp.pages_per_request <= 1000000; break;
        case O_HOTF: ok = parse_fraction(optarg, &pp.hot_fraction); break;
        case O_HOTP: ok = parse_fraction(optarg, &pp.hot_probability); break;
        case O_WF:   ok = parse_fraction(optarg, &pp.write_fraction); break;
        case O_SEED: ok = parse_u64(optarg, &pp.seed); break;
        case O_JSON: ok = optarg && *optarg; pp.json_out = optarg; break;
        case O_CORRUPT: pp.corrupt_self_test = true; break;
        case O_DIGEST:  pp.print_digest = true; break;
        case O_HELP: usage(stdout); return EXIT_OK;
        default: usage(stderr); return EXIT_USAGE;
        }
        if (!ok) {
            fprintf(stderr, "pagestress: invalid value for --%s: '%s'\n",
                    longopts[c - O_SIZE].name, optarg ? optarg : "");
            usage(stderr);
            return EXIT_USAGE;
        }
    }
    if (optind < argc) {
        fprintf(stderr, "pagestress: unexpected argument '%s'\n", argv[optind]);
        usage(stderr);
        return EXIT_USAGE;
    }
    if (!have_size) {
        fprintf(stderr, "pagestress: --size-mib N (1..%" PRIu64 ") is required\n", MAX_SIZE_MIB);
        usage(stderr);
        return EXIT_USAGE;
    }
    if (pp.requests == 0 && pp.duration_s <= 0.0) {
        fprintf(stderr, "pagestress: at least one of --requests / --duration-s must be > 0\n");
        usage(stderr);
        return EXIT_USAGE;
    }

    long sys_page = sysconf(_SC_PAGESIZE);
    if (sys_page != (long)PAGE_SIZE_REQUIRED) {
        fprintf(stderr, "pagestress: system page size %ld unsupported (need 4096)\n", sys_page);
        return EXIT_SYS;
    }

    /* Open the JSON output early so a bad path fails before a long run. */
    FILE *jf = NULL;
    if (pp.json_out) {
        jf = fopen(pp.json_out, "w");
        if (!jf) {
            fprintf(stderr, "pagestress: cannot open --json-out '%s': %s\n",
                    pp.json_out, strerror(errno));
            return EXIT_SYS;
        }
    }

    struct results r;
    memset(&r, 0, sizeof r);
    const uint64_t page_count = pp.size_mib * (1024u * 1024u / PAGE_SIZE_REQUIRED);
    const uint64_t bytes = page_count * PAGE_SIZE_REQUIRED; /* <= 16 TiB, fits */
    r.page_count = page_count;

    /* Data buffer: private anonymous, demand-faulted, swappable (no POPULATE/mlock). */
    void *map = mmap(NULL, (size_t)bytes, PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (map == MAP_FAILED) {
        fprintf(stderr, "pagestress: mmap of %" PRIu64 " bytes failed: %s\n", bytes, strerror(errno));
        return EXIT_SYS;
    }
    uint64_t *buf = map;

    /* Metadata (part of the workload's own RAM; 12 bytes/page). */
    uint64_t *expected_hash = malloc((size_t)page_count * sizeof *expected_hash);
    uint32_t *versions = malloc((size_t)page_count * sizeof *versions);

    /* Hot set: first hot_count entries of a seeded Fisher-Yates shuffle, so hot
     * pages are scattered across the mapping. The full permutation is only
     * temporary; we keep just the hot prefix. */
    uint64_t hot_count = (uint64_t)(pp.hot_fraction * (double)page_count);
    if (pp.hot_fraction > 0.0 && hot_count == 0)
        hot_count = 1;
    if (hot_count > page_count)
        hot_count = page_count;
    r.hot_count = hot_count;
    uint32_t *hot = malloc((size_t)(hot_count ? hot_count : 1) * sizeof *hot);

    /* Latency samples: allocated lazily-faulted (large malloc => mmap), so only
     * entries actually written become resident. */
    uint64_t lat_cap = LATENCY_CAP;
    if (pp.requests > 0 && pp.requests < lat_cap)
        lat_cap = pp.requests;
    uint64_t *lat = malloc((size_t)lat_cap * sizeof *lat);

    if (!expected_hash || !versions || !hot || !lat) {
        fprintf(stderr, "pagestress: metadata allocation failed\n");
        return EXIT_SYS;
    }

    uint64_t rng_state = pp.seed;
    uint64_t rng = splitmix64(&rng_state);
    if (rng == 0)
        rng = 1;

    if (hot_count > 0) {
        uint32_t *perm = malloc((size_t)page_count * sizeof *perm);
        if (!perm) {
            fprintf(stderr, "pagestress: permutation allocation failed\n");
            return EXIT_SYS;
        }
        for (uint64_t i = 0; i < page_count; i++)
            perm[i] = (uint32_t)i;
        for (uint64_t i = 0; i < hot_count; i++) { /* partial Fisher-Yates */
            uint64_t j = i + rand_below(&rng, page_count - i);
            uint32_t t = perm[i]; perm[i] = perm[j]; perm[j] = t;
        }
        memcpy(hot, perm, (size_t)hot_count * sizeof *hot);
        free(perm);
    }

    /* ---- Phase 1: populate (version 0 everywhere). */
    uint64_t t0 = now_ns();
    for (uint64_t i = 0; i < page_count; i++)
        write_page(buf + i * WORDS_PER_PAGE, i, 0, pp.seed, expected_hash, versions);
    uint64_t t1 = now_ns();
    r.populate_s = (double)(t1 - t0) / 1e9;

    if (pp.print_digest) {
        /* Digest over expected hashes: identical seeds => identical content. */
        uint64_t d = 0xCBF29CE484222325ull;
        for (uint64_t i = 0; i < page_count; i++) {
            d ^= expected_hash[i];
            d *= 0x100000001B3ull;
        }
        r.digest = d;
        fprintf(stderr, "pagestress: populate_digest=0x%016" PRIx64 "\n", d);
    }

    if (pp.corrupt_self_test) {
        /* Deliberately bypass write_page(): flip one byte in the payload area of
         * a populated page without touching expected_hash[]. Must be detected. */
        uint64_t victim = page_count / 2;
        volatile unsigned char *b = (volatile unsigned char *)(buf + victim * WORDS_PER_PAGE);
        b[100] ^= 0x01;
        fprintf(stderr, "pagestress: self-test corrupted page %" PRIu64 " byte 100\n", victim);
    }

    /* ---- Phase 2: requests. */
    const uint64_t k = pp.pages_per_request;
    const uint64_t dur_ns = pp.duration_s > 0 ? (uint64_t)(pp.duration_s * 1e9) : 0;
    uint64_t lat_sum = 0, lat_max = 0, reported = 0, n = 0;
    uint64_t t_req0 = now_ns(), last_progress = t_req0, t_now = t_req0;

    while (!g_stop) {
        if (pp.requests > 0 && n >= pp.requests)
            break;
        if (dur_ns && t_now - t_req0 >= dur_ns)
            break;

        uint64_t rs = now_ns();
        for (uint64_t a = 0; a < k; a++) {
            uint64_t idx;
            if (hot_count > 0 && rand_unit(&rng) < pp.hot_probability)
                idx = hot[rand_below(&rng, hot_count)];
            else
                idx = rand_below(&rng, page_count);

            uint64_t *page = buf + idx * WORDS_PER_PAGE;
            uint64_t h = hash_page(page);
            if (h != expected_hash[idx]) {
                /* Count, do not abort, and do not rewrite: leave the evidence
                 * in place so the final sweep sees it too. */
                r.verify_errors++;
                report_mismatch("verify", idx, page, expected_hash[idx], h, versions[idx], &reported);
                continue;
            }
            if (rand_unit(&rng) < pp.write_fraction)
                write_page(page, idx, versions[idx] + 1, pp.seed, expected_hash, versions);
        }
        t_now = now_ns();
        uint64_t l = t_now - rs;
        if (n < lat_cap)
            lat[n] = l;
        else
            r.latency_truncated = true;
        lat_sum += l;
        if (l > lat_max)
            lat_max = l;
        n++;

        if (t_now - last_progress >= PROGRESS_INTERVAL_NS) {
            last_progress = t_now;
            fprintf(stderr, "pagestress: progress requests=%" PRIu64 " elapsed_s=%.1f verify_errors=%" PRIu64 "\n",
                    n, (double)(t_now - t_req0) / 1e9, r.verify_errors);
        }
    }
    uint64_t t_req1 = now_ns();
    r.interrupted = g_stop != 0;
    r.requests_completed = n;
    r.request_phase_s = (double)(t_req1 - t_req0) / 1e9;

    /* ---- Phase 3: final sweep over every page (runs even when interrupted). */
    uint64_t t2 = now_ns();
    for (uint64_t i = 0; i < page_count; i++) {
        uint64_t *page = buf + i * WORDS_PER_PAGE;
        uint64_t h = hash_page(page);
        if (h != expected_hash[i]) {
            r.final_sweep_errors++;
            report_mismatch("final", i, page, expected_hash[i], h, versions[i], &reported);
        }
    }
    uint64_t t3 = now_ns();
    r.final_sweep_s = (double)(t3 - t2) / 1e9;

    /* Latency statistics. Percentiles: nearest-rank over recorded samples
     * (first LATENCY_CAP requests). mean and max cover ALL requests. */
    r.lat_recorded = n < lat_cap ? n : lat_cap;
    if (r.lat_recorded > 0) {
        qsort(lat, (size_t)r.lat_recorded, sizeof *lat, cmp_u64); /* in place: samples no longer needed */
        r.p50 = (double)percentile_nr(lat, r.lat_recorded, 50.0) / 1e6;
        r.p90 = (double)percentile_nr(lat, r.lat_recorded, 90.0) / 1e6;
        r.p99 = (double)percentile_nr(lat, r.lat_recorded, 99.0) / 1e6;
        r.p999 = (double)percentile_nr(lat, r.lat_recorded, 99.9) / 1e6;
    }
    r.lat_max = (double)lat_max / 1e6;
    r.lat_mean = n ? ((double)lat_sum / (double)n) / 1e6 : 0.0;

    if (getrusage(RUSAGE_SELF, &r.ru) != 0)
        memset(&r.ru, 0, sizeof r.ru);
    r.total_s = (double)(now_ns() - t_start) / 1e9;

    print_json(stdout, &pp, &r);
    fflush(stdout);
    int rc = (r.verify_errors || r.final_sweep_errors) ? EXIT_VERIFY : EXIT_OK;
    if (jf) {
        print_json(jf, &pp, &r);
        if (fclose(jf) != 0) {
            fprintf(stderr, "pagestress: writing --json-out failed: %s\n", strerror(errno));
            if (rc == EXIT_OK)
                rc = EXIT_SYS;
        }
    }

    free(lat);
    free(hot);
    free(versions);
    free(expected_hash);
    munmap(map, (size_t)bytes);
    return rc;
}
