// 新评分独立评价三类质量；旧四项分数只用于读取，不换算、不补填。
export const REVIEW_RUBRIC_VERSION = 'independent-review-v2'
export const reviewDimensions = {
  discovery_breadth: '发现广度',
  chain_verification: '链路核验',
  conclusion_credibility: '结论可信度',
}
export const legacyReviewDimensions = {
  correctness: '正确性', completeness: '完整性', evidence: '证据充分', hallucination_control: '幻觉控制',
}
export const emptyReviewScores = () => Object.fromEntries(Object.keys(reviewDimensions).map((key) => [key, 0]))
export const savedReviewDimensions = (review) => review?.scores?.rubric_version === REVIEW_RUBRIC_VERSION
  ? reviewDimensions : legacyReviewDimensions
