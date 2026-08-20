export function deriveSubmissionState(value = {}) {
  const busy = Boolean(value.busy);
  const confirmed = Boolean(value.confirmed);
  const suggestedCategoryCount = Number(value.suggested_category_count || 0);
  const manualCategoryCount = Number(value.manual_category_count || 0);
  const correctedCategoryCount = Number(value.corrected_category_count || 0);
  const needsManualCategories = suggestedCategoryCount === 0;

  return {
    canApprove: !busy && confirmed && !needsManualCategories,
    canCompleteCategories:
      !busy && confirmed && needsManualCategories && manualCategoryCount > 0,
    canCorrect: !busy && confirmed && correctedCategoryCount > 0,
    needsManualCategories,
  };
}
