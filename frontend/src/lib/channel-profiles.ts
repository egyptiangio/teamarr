export function profileIdsToApi(
  selectedIds: (number | string)[],
  allProfileIds: number[]
): (number | string)[] | null {
  if (selectedIds.length === 0) {
    return []; // No profiles
  }

  const numericIds = selectedIds.filter((x): x is number => typeof x === 'number');
  const wildcardIds = selectedIds.filter((x): x is string => typeof x === 'string');

  const selectedSet = new Set(numericIds);
  const allSelected = allProfileIds.length > 0 &&
    allProfileIds.every((id) => selectedSet.has(id));

  if (allSelected && wildcardIds.length === 0) {
    return null;
  }

  return selectedIds;
}

export function apiToProfileIds(
  apiValue: (number | string)[] | null | undefined,
  allProfileIds: number[]
): (number | string)[] {
  if (apiValue === null || apiValue === undefined) {
    return [...allProfileIds];
  }
  if (apiValue.length === 1 && apiValue[0] === 0) {
    return [...allProfileIds];
  }
  return apiValue;
}
