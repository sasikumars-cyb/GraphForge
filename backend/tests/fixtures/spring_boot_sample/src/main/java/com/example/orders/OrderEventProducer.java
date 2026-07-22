package com.example.orders;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;

@Component
public class OrderEventProducer {

    @Autowired
    private KafkaTemplate<String, String> kafkaTemplate;

    public void publishOrderCreated(String payload) {
        kafkaTemplate.send("order-created", payload);
    }

    public void publishOrderCancelled(String payload) {
        this.kafkaTemplate.send("order-cancelled", payload);
    }
}
