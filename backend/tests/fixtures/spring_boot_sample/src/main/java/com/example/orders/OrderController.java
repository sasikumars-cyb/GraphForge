package com.example.orders;

import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/orders")
public class OrderController {

    @GetMapping
    public String listOrders() {
        return "[]";
    }

    @GetMapping("/{id}")
    public String getOrder(@PathVariable String id) {
        return "{}";
    }

    @PostMapping("/create")
    public String createOrder() {
        return "created";
    }

    @DeleteMapping("/{id}")
    public void deleteOrder(@PathVariable String id) {
    }
}
