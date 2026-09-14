/**
 * The disposable-target guard.
 *
 * This is not harness polish. The integration seed issues
 * `DELETE FROM memories` and `DELETE FROM memory_conflicts` for its workspace,
 * and the only thing that used to keep it away from real data was that the
 * connection was hardcoded to one developer's throwaway cluster. Making the
 * harness portable removed that accident, so the guard now has to do the job
 * deliberately — and a guard nothing tests is a guard nobody knows is broken.
 *
 * Runs in the ordinary unit suite, on every machine, with no services.
 */

const { _internals } = require("../../e2e/psqlClient");

const { config } = _internals;

/** A configuration that should be accepted, so each case varies one field. */
const OK = {
    E2E_ALLOW_DISPOSABLE: "1",
    E2E_PG_USER: "postgres",
    E2E_PG_DATABASE: "sourcemind_e2e",
};

describe("the opt-in flag", () => {
    test("absent flag refuses, however disposable the name looks", () => {
        expect(() => config({ ...OK, E2E_ALLOW_DISPOSABLE: undefined }))
            .toThrow(/E2E_ALLOW_DISPOSABLE/);
    });

    test("a truthy-but-wrong value is not an opt-in", () => {
        for (const value of ["true", "yes", "0", ""]) {
            expect(() => config({ ...OK, E2E_ALLOW_DISPOSABLE: value }))
                .toThrow(/E2E_ALLOW_DISPOSABLE/);
        }
    });

    test('"1" with a disposable name is accepted', () => {
        expect(config(OK).database).toBe("sourcemind_e2e");
    });
});

describe("protected names are refused even WITH the flag", () => {
    // The flag says "I meant to point this somewhere disposable". It is not a
    // licence to run a destructive seed against production because a variable
    // was wrong.
    test.each([
        "sourcemind_production",
        "sourcemind-prod",
        "live_db",
        "main",
        "staging_e2e",
    ])("%s", (database) => {
        expect(() => config({ ...OK, E2E_PG_DATABASE: database }))
            .toThrow(/protected name/);
    });
});

describe("names that are not recognisably disposable are refused", () => {
    test.each(["sourcemind", "app", "postgres", "customer_data"])("%s", (database) => {
        expect(() => config({ ...OK, E2E_PG_DATABASE: database }))
            .toThrow(/not\s+recognisably disposable/);
    });

    test.each([
        "sourcemind_e2e",
        "sourcemind_test",
        "e2e",
        "ci_scratch",
        "web-e2e-db",
    ])("%s is accepted", (database) => {
        expect(config({ ...OK, E2E_PG_DATABASE: database }).database).toBe(database);
    });
});

describe("required fields", () => {
    test("a missing database is refused before any name check", () => {
        expect(() => config({ ...OK, E2E_PG_DATABASE: undefined }))
            .toThrow(/E2E_PG_DATABASE/);
    });

    test("a missing user is refused", () => {
        expect(() => config({ ...OK, E2E_PG_USER: undefined }))
            .toThrow(/E2E_PG_USER/);
    });
});

describe("mode selection", () => {
    const { resolveMode } = _internals;

    test("an explicit mode is honoured", () => {
        expect(resolveMode({ E2E_PSQL_MODE: "direct" })).toBe("direct");
        expect(resolveMode({ E2E_PSQL_MODE: "WSL" })).toBe("wsl");
    });

    test("an unknown mode fails loudly rather than falling back", () => {
        // Silently defaulting would send a CI run down the WSL path and fail
        // with "wsl.exe not found" instead of naming the real mistake.
        expect(() => resolveMode({ E2E_PSQL_MODE: "docker" }))
            .toThrow(/must be "direct" or "wsl"/);
    });

    test("with no mode set, the platform decides", () => {
        // On the Linux CI runner this is "direct"; on the Windows development
        // machine it is "wsl". Asserting against the current platform keeps
        // this true in both places.
        const expected = process.platform === "win32" ? "wsl" : "direct";
        expect(resolveMode({})).toBe(expected);
    });
});

describe("secret redaction", () => {
    const { redact } = _internals;

    test("every occurrence of the password is removed", () => {
        const pw = "s3cr3t-p@ss";
        const text = `connection to db failed: password=${pw} retrying with ${pw}`;
        const out = redact(text, pw);
        expect(out).not.toContain(pw);
        expect(out.match(/«redacted»/g)).toHaveLength(2);
    });

    test("a password containing regex metacharacters is still removed", () => {
        // The reason redaction is split/join and not a RegExp: a generated
        // password can contain anything.
        const pw = "a.*b[c]$d\\e";
        expect(redact(`fail: ${pw}`, pw)).not.toContain(pw);
    });

    test("an empty password redacts nothing rather than everything", () => {
        // Splitting on "" would otherwise insert the marker between every
        // character and destroy the diagnostic.
        expect(redact("plain message", "")).toBe("plain message");
    });
});

describe("how psql is actually invoked", () => {
    const { createPsql } = require("../../e2e/psqlClient");

    const BASE = {
        E2E_ALLOW_DISPOSABLE: "1",
        E2E_PG_USER: "sourcemind_bootstrap",
        E2E_PG_DATABASE: "sourcemind_e2e",
        E2E_PG_HOST: "127.0.0.1",
        E2E_PG_PORT: "5432",
        E2E_PG_PASSWORD: "p@ss w'rd $ecret",
    };

    /** Records the call instead of running it, and returns a canned row. */
    function recorder(result = "row\n") {
        const calls = [];
        const exec = (file, argv, opts) => {
            calls.push({ file, argv, env: opts.env });
            return result;
        };
        return { calls, exec };
    }

    test("direct mode runs psql itself, with the query as one argument", () => {
        const { calls, exec } = recorder();
        createPsql({ ...BASE, E2E_PSQL_MODE: "direct" }, exec)("SELECT 1");

        expect(calls).toHaveLength(1);
        expect(calls[0].file).toBe("psql");
        expect(calls[0].argv).toEqual([
            "-h", "127.0.0.1", "-p", "5432",
            "-U", "sourcemind_bootstrap", "-d", "sourcemind_e2e",
            "-v", "ON_ERROR_STOP=1", "-Atc", "SELECT 1",
        ]);
    });

    test("direct mode puts the password in the child environment, never in argv", () => {
        const { calls, exec } = recorder();
        createPsql({ ...BASE, E2E_PSQL_MODE: "direct" }, exec)("SELECT 1");

        // The whole point: a password in argv is visible in `ps` to every
        // other process on the host.
        expect(calls[0].argv.join(" ")).not.toContain(BASE.E2E_PG_PASSWORD);
        expect(calls[0].env.PGPASSWORD).toBe(BASE.E2E_PG_PASSWORD);
    });

    test("a custom psql path is honoured", () => {
        const { calls, exec } = recorder();
        createPsql(
            { ...BASE, E2E_PSQL_MODE: "direct", E2E_PSQL: "/usr/lib/postgresql/16/bin/psql" },
            exec
        )("SELECT 1");
        expect(calls[0].file).toBe("/usr/lib/postgresql/16/bin/psql");
    });

    test("wsl mode assigns the password via env, not inside a shell string", () => {
        const { calls, exec } = recorder();
        createPsql({ ...BASE, E2E_PSQL_MODE: "wsl" }, exec)("SELECT 1");

        expect(calls[0].file).toBe("wsl.exe");
        // `wsl -e env NAME=value psql …` — no `bash -lc`, so the password is
        // never part of a string a shell will re-parse. The old harness
        // interpolated a command substitution into exactly such a string.
        expect(calls[0].argv.slice(0, 3)).toEqual([
            "-e", "env", `PGPASSWORD=${BASE.E2E_PG_PASSWORD}`,
        ]);
        expect(calls[0].argv).not.toContain("bash");
        expect(calls[0].argv).toContain("SELECT 1");
    });

    test("output is trimmed and CR-stripped, so Windows line endings do not leak", () => {
        const { exec } = recorder("t|f|\r\n");
        const out = createPsql({ ...BASE, E2E_PSQL_MODE: "wsl" }, exec)("SELECT TRUE, FALSE, NULL");
        expect(out).toBe("t|f|");
    });

    test("a failure is rethrown with the password scrubbed out", () => {
        const exec = () => {
            const err = new Error(`connection failed for PGPASSWORD=${BASE.E2E_PG_PASSWORD}`);
            err.stderr = `psql: FATAL: password authentication failed (${BASE.E2E_PG_PASSWORD})`;
            throw err;
        };
        const sql = createPsql({ ...BASE, E2E_PSQL_MODE: "direct" }, exec);

        expect(() => sql("SELECT 1")).toThrow(/psql \(direct\) failed/);
        try {
            sql("SELECT 1");
        } catch (e) {
            expect(e.message).not.toContain(BASE.E2E_PG_PASSWORD);
            expect(e.message).toContain("«redacted»");
            expect(e.stack).not.toContain(BASE.E2E_PG_PASSWORD);
        }
    });
});
