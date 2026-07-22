package com.example.orders;

import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;

@FeignClient(name = "payment-service", url = "http://payment-service:8080")
public interface PaymentClient {

    @PostMapping("/api/payments/charge")
    String charge();

    @GetMapping("/api/payments/{id}")
    String getPayment();
}
