//
//  Item.swift
//  AppleBooks
//
//  Created by Himanshu Jain on 20/08/2026.
//

import Foundation
import SwiftData

@Model
final class Item {
    var timestamp: Date
    
    init(timestamp: Date) {
        self.timestamp = timestamp
    }
}
