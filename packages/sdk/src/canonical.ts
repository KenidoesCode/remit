/**
 * Number-literal-preserving JSON, so a receipt can actually be verified.
 *
 * THE PROBLEM
 * -----------
 * The server hashes `json.dumps(..., sort_keys=True, separators=(",", ":"))`.
 * Python writes a float `0.0` as `0.0`. JavaScript's `JSON.parse` turns that
 * into the number `0`, and `0` and `0.0` are the same value in JS — the
 * literal is gone before any encoder can see it. Re-serialising gives `0`, the
 * bytes differ, and every hash over a payload containing a whole-number float
 * fails to match.
 *
 * This is not hypothetical: REMIT's `DRIFT_MEASURED` payload is twelve
 * dimensions that are almost always exactly `0.0`, so it was every receipt.
 *
 * The first version of the verifier used JSON.parse and reported
 * `hash mismatch at seq 38` against a chain that was completely intact. A
 * verifier that cries tampering on healthy data is worse than no verifier: it
 * trains you to ignore it.
 *
 * THE FIX
 * -------
 * Parse the wire text with a parser that keeps every number as the exact
 * substring it occupied, and emit that substring verbatim when re-encoding.
 * Nothing is guessed and nothing is normalised.
 */

/** A number kept as the characters it arrived as. */
export class RawNumber {
  constructor(readonly literal: string) {}
  valueOf(): number {
    return Number(this.literal);
  }
  toJSON(): number {
    return Number(this.literal);
  }
}

/** JSON.parse, except numbers become RawNumber. */
export function parsePreservingNumbers(text: string): unknown {
  const p = new Parser(text);
  const value = p.parseValue();
  p.skipWhitespace();
  if (!p.atEnd()) throw new SyntaxError(`unexpected trailing input at ${p.pos}`);
  return value;
}

class Parser {
  pos = 0;
  constructor(private readonly s: string) {}

  atEnd(): boolean {
    return this.pos >= this.s.length;
  }

  skipWhitespace(): void {
    while (this.pos < this.s.length) {
      const c = this.s.charCodeAt(this.pos);
      if (c === 0x20 || c === 0x09 || c === 0x0a || c === 0x0d) this.pos++;
      else break;
    }
  }

  private expect(ch: string): void {
    if (this.s[this.pos] !== ch) {
      throw new SyntaxError(`expected ${ch} at ${this.pos}, found ${this.s[this.pos]}`);
    }
    this.pos++;
  }

  parseValue(): unknown {
    this.skipWhitespace();
    const c = this.s[this.pos];
    if (c === "{") return this.parseObject();
    if (c === "[") return this.parseArray();
    if (c === '"') return this.parseString();
    if (this.s.startsWith("true", this.pos)) {
      this.pos += 4;
      return true;
    }
    if (this.s.startsWith("false", this.pos)) {
      this.pos += 5;
      return false;
    }
    if (this.s.startsWith("null", this.pos)) {
      this.pos += 4;
      return null;
    }
    return this.parseNumber();
  }

  private parseObject(): Record<string, unknown> {
    this.expect("{");
    const obj: Record<string, unknown> = {};
    this.skipWhitespace();
    if (this.s[this.pos] === "}") {
      this.pos++;
      return obj;
    }
    for (;;) {
      this.skipWhitespace();
      const key = this.parseString();
      this.skipWhitespace();
      this.expect(":");
      obj[key] = this.parseValue();
      this.skipWhitespace();
      if (this.s[this.pos] === ",") {
        this.pos++;
        continue;
      }
      this.expect("}");
      return obj;
    }
  }

  private parseArray(): unknown[] {
    this.expect("[");
    const arr: unknown[] = [];
    this.skipWhitespace();
    if (this.s[this.pos] === "]") {
      this.pos++;
      return arr;
    }
    for (;;) {
      arr.push(this.parseValue());
      this.skipWhitespace();
      if (this.s[this.pos] === ",") {
        this.pos++;
        continue;
      }
      this.expect("]");
      return arr;
    }
  }

  private parseString(): string {
    this.expect('"');
    let out = "";
    for (;;) {
      const ch = this.s[this.pos];
      if (ch === undefined) throw new SyntaxError("unterminated string");
      if (ch === '"') {
        this.pos++;
        return out;
      }
      if (ch === "\\") {
        this.pos++;
        const esc = this.s[this.pos++];
        switch (esc) {
          case '"': out += '"'; break;
          case "\\": out += "\\"; break;
          case "/": out += "/"; break;
          case "b": out += "\b"; break;
          case "f": out += "\f"; break;
          case "n": out += "\n"; break;
          case "r": out += "\r"; break;
          case "t": out += "\t"; break;
          case "u": {
            const hex = this.s.slice(this.pos, this.pos + 4);
            this.pos += 4;
            out += String.fromCharCode(Number.parseInt(hex, 16));
            break;
          }
          default:
            throw new SyntaxError(`bad escape \\${esc}`);
        }
        continue;
      }
      out += ch;
      this.pos++;
    }
  }

  private parseNumber(): RawNumber {
    const start = this.pos;
    if (this.s[this.pos] === "-") this.pos++;
    while (this.pos < this.s.length && /[0-9]/.test(this.s[this.pos] as string)) this.pos++;
    if (this.s[this.pos] === ".") {
      this.pos++;
      while (this.pos < this.s.length && /[0-9]/.test(this.s[this.pos] as string)) this.pos++;
    }
    if (this.s[this.pos] === "e" || this.s[this.pos] === "E") {
      this.pos++;
      if (this.s[this.pos] === "+" || this.s[this.pos] === "-") this.pos++;
      while (this.pos < this.s.length && /[0-9]/.test(this.s[this.pos] as string)) this.pos++;
    }
    const literal = this.s.slice(start, this.pos);
    if (literal === "" || literal === "-") throw new SyntaxError(`bad number at ${start}`);
    return new RawNumber(literal);
  }
}

/** Strip RawNumber wrappers, for handing plain data back to callers. */
export function toPlain(value: unknown): unknown {
  if (value instanceof RawNumber) return Number(value.literal);
  if (Array.isArray(value)) return value.map(toPlain);
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value as Record<string, unknown>)) out[k] = toPlain(v);
    return out;
  }
  return value;
}
