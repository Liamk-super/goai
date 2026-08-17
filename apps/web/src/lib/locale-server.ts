import { cookies } from "next/headers";

import { normalizeLocale, type Locale } from "./i18n";

const LOCALE_COOKIE = "launchscope.locale";

export async function requestLocale(): Promise<Locale> {
  const cookieStore = await cookies();
  return normalizeLocale(cookieStore.get(LOCALE_COOKIE)?.value);
}

export function localizedMetadata(
  locale: Locale,
  values: { en: { title: string; description: string }; "zh-CN": { title: string; description: string } },
) {
  return values[locale];
}
