/**
 * The rate limits on the sign-in and register forms (FR-36, step 14): a
 * 429 is shown in the API's own words, which say which limit it is and
 * when it lifts.
 */
import { describe, expect, test } from "vitest";
import { said } from "@/components/account/form";
import { ApiError } from "@/lib/api";

describe("a 429 on the account forms", () => {
  test("login_limit: the API's words, whole", () => {
    const message =
      "Too many failed sign-ins for this email: 5 in 15 minutes is the most allowed. You can try to sign in again in 15 minutes.";
    expect(said(new ApiError(429, "login_limit", message))).toBe(message);
  });

  test("login_ceiling and register_limit: the API's words, whole", () => {
    for (const code of ["login_ceiling", "register_limit"]) {
      const message = `${code}: the limit, then when it lifts. You can register again in 3 minutes.`;
      expect(said(new ApiError(429, code, message))).toBe(message);
    }
  });
});
