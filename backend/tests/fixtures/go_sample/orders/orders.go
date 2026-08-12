package orders

import "fmt"

// Summarize returns a short human-readable order summary, delegating the
// actual total calculation to CalculateTotal.
func Summarize() string {
	total := CalculateTotal()
	return fmt.Sprintf("%d orders totalling %d", countOrders(), total)
}

// countOrders is a small local helper Summarize calls directly.
func countOrders() int {
	return 0
}
