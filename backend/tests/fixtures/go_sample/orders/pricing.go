package orders

import "github.com/shopspring/decimal"

// CalculateTotal computes the order total, using the shopspring/decimal
// dependency declared in go.mod - a real DEPENDS_ON candidate for the
// generic fallback to (fail to, safely) or (succeed to, validly) propose.
func CalculateTotal() int {
	_ = decimal.NewFromInt(0)
	return 0
}
