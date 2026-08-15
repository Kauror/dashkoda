/**
 * Every routed page, with the heading that proves it actually rendered.
 *
 * `withoutFigures` marks a page the seed legitimately leaves without a number.
 * The content suite otherwise asserts that every page carries a digit, which is
 * how it notices a seed that never reached one — a check that is meaningless
 * where the page is empty on purpose.
 */
export const PAGES = [
  { name: "the overview", path: "/", heading: "Koja töölaud" },
  { name: "Liikmeskond", path: "/liikmeskond/" },
  { name: "Õigusloome", path: "/oigusloome/" },
  { name: "Sündmused", path: "/sundmused/" },
  { name: "Uudised", path: "/uudised/" },
  { name: "Koduleht", path: "/koduleht/" },
  { name: "E-pood", path: "/epood/" },
  { name: "Otsepostitused", path: "/otsepostitused/" },
  // No Smaily data is seeded in either suite, so the send archive renders its
  // empty state and states a count of nothing in words.
  { name: "Otsepostituste ajalugu", path: "/otsepostitused/ajalugu/", withoutFigures: true },
  // Admin is a foundation page and carries no figure at all by design.
  { name: "Admin", path: "/haldus/", heading: "Admin", withoutFigures: true },
];
