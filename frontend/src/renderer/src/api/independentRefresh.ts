export function applyIndependently<T>(
  request: Promise<T>,
  onFulfilled: (value: T) => void,
  onRejected?: (reason: unknown) => void
): Promise<void> {
  return request.then(
    (value) => onFulfilled(value),
    (reason) => onRejected?.(reason)
  )
}
