package com.example.orders;

import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

@Component
public class OrderEventListener {

    @KafkaListener(topics = {"order-created", "order-updated"}, groupId = "orders-group")
    public void onOrderEvent(String payload) {
    }
}
